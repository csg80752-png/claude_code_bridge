from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from provider_backends.codex.comm_runtime.log_entries import extract_entry

from .state_machine_runtime.models import CodexPollState

# v8.4 PR #2 (diag round) — non-behavioral. Spec: docs/v8.4-plan.md lines 58-65.
# When a codex execution wedges (binding contaminated, reply_buffer empty,
# session jsonl past offset has unread content), emit a single warning per
# (job_id, ccbd_lifetime) capturing offset / binding fields / last entry /
# which guard would reject the next match. The Issue #1 fix PR (v8.4-readback)
# reads these warnings to lock the fix shape (force-rebind vs trailing-read
# vs abandon-and-quarantine).

_logger = logging.getLogger(__name__)

# Process-global one-shot gate. Resets naturally on ccbd restart (which is
# precisely when we want to re-fire the diag for any wedge that survives
# the restart).
_emitted: set[str] = set()

# Tail read budget for the last-entry summary. Codex jsonl entries are
# typically <2KB; 16KB / 4 lines covers the worst-case multi-line tool
# call entry without making this a memory hazard.
_TAIL_MAX_BYTES = 16 * 1024
_TAIL_MAX_LINES = 4

# Truncate text fields in the last-entry summary so a 100KB tool-call
# payload doesn't blow up ccbd.stderr.
_SUMMARY_TEXT_PREVIEW = 240


def maybe_emit_binding_diag(
    submission,
    poll: CodexPollState,
    state: dict[str, object],
    *,
    pre_poll_state: dict[str, object] | None = None,
) -> None:
    if not _is_wedge_condition(poll):
        return

    job_id = str(getattr(submission, 'job_id', '') or '').strip()
    if not job_id or job_id in _emitted:
        return

    log_path = _coerce_path(state.get('log_path'))
    post_offset = _coerce_int(state.get('offset'))
    pre_offset = _coerce_int((pre_poll_state or {}).get('offset')) if pre_poll_state else -1
    file_size = _stat_size(log_path)
    lines_consumed_this_tick = _count_lines_in_range(log_path, pre_offset, post_offset)
    lines_past_post_offset = _count_lines_past_offset(log_path, post_offset)
    last_entry = _summarize_last_entry(log_path)
    predicate_blocker = _predicate_blocker(poll)
    requires_turn_id_envvar = os.environ.get('CCB_CODEX_REQUIRES_TURN_ID')

    _emitted.add(job_id)

    _logger.warning(
        'v8.4-diag binding-wedge job=%s log_path=%s pre_poll_offset=%s post_poll_offset=%s '
        'file_size=%s lines_consumed_this_tick=%s lines_past_post_offset=%s '
        'anchor_seen=%s '
        'bound_turn_id=%r current_turn_id=%r requires_turn_id=%s '
        'bound_turn_contaminated=%s bound_turn_started=%s current_turn_started=%s '
        'reply_buffer_len=%d last_assistant_message_len=%d '
        'predicate_blocker=%s requires_turn_id_envvar=%r '
        'last_entry=%s',
        job_id,
        log_path,
        pre_offset,
        post_offset,
        file_size,
        lines_consumed_this_tick,
        lines_past_post_offset,
        poll.anchor_seen,
        poll.bound_turn_id,
        poll.current_turn_id,
        poll.requires_turn_id,
        poll.bound_turn_contaminated,
        poll.bound_turn_started,
        poll.current_turn_started,
        len(poll.reply_buffer or ''),
        len(poll.last_assistant_message or ''),
        predicate_blocker,
        requires_turn_id_envvar,
        last_entry,
    )


def _is_wedge_condition(poll: CodexPollState) -> bool:
    if poll.reached_terminal:
        return False
    if not poll.bound_turn_contaminated:
        return False
    if poll.reply_buffer:
        return False
    return True


def _predicate_blocker(poll: CodexPollState) -> str:
    if not poll.anchor_seen:
        return 'anchor_not_seen'
    if poll.requires_turn_id and poll.bound_turn_contaminated and not poll.bound_turn_id:
        return 'requires_turn_id_unsatisfied'
    if poll.bound_turn_contaminated:
        return 'bound_turn_contaminated'
    if (
        poll.bound_turn_id
        and poll.current_turn_id
        and poll.current_turn_id != poll.bound_turn_id
    ):
        return 'turn_id_mismatch'
    if poll.bound_turn_id and not poll.bound_turn_started:
        return 'bound_turn_not_started'
    if not poll.bound_turn_id:
        return 'no_bound_turn_id'
    return 'no_blocker_inferred'


def _coerce_path(value: object) -> Path | None:
    if value is None:
        return None
    if isinstance(value, Path):
        return value
    text = str(value).strip()
    if not text:
        return None
    return Path(text)


def _coerce_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return -1


def _stat_size(log_path: Path | None) -> int:
    if log_path is None:
        return -1
    try:
        return log_path.stat().st_size
    except OSError:
        return -1


def _count_lines_past_offset(log_path: Path | None, offset: int) -> int:
    if log_path is None or offset < 0:
        return -1
    try:
        with log_path.open('rb') as handle:
            handle.seek(offset)
            count = 0
            while True:
                chunk = handle.read(64 * 1024)
                if not chunk:
                    break
                count += chunk.count(b'\n')
            return count
    except OSError:
        return -1


def _count_lines_in_range(log_path: Path | None, start: int, end: int) -> int:
    if log_path is None or start < 0 or end < 0 or end < start:
        return -1
    if end == start:
        return 0
    try:
        with log_path.open('rb') as handle:
            handle.seek(start)
            remaining = end - start
            count = 0
            while remaining > 0:
                chunk = handle.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                count += chunk.count(b'\n')
                remaining -= len(chunk)
            return count
    except OSError:
        return -1


def _summarize_last_entry(log_path: Path | None) -> str:
    if log_path is None:
        return 'no_log_path'
    try:
        with log_path.open('rb') as handle:
            handle.seek(0, os.SEEK_END)
            position = handle.tell()
            buffer = b''
            lines_collected = 0
            while position > 0 and len(buffer) < _TAIL_MAX_BYTES and lines_collected < _TAIL_MAX_LINES:
                read_size = min(8192, position)
                position -= read_size
                handle.seek(position, os.SEEK_SET)
                chunk = handle.read(read_size)
                buffer = chunk + buffer
                lines_collected = buffer.count(b'\n')
            tail_lines = [line for line in buffer.split(b'\n') if line.strip()]
        if not tail_lines:
            return 'empty_tail'
        last_raw = tail_lines[-1]
        try:
            decoded = last_raw.decode('utf-8', errors='replace')
        except Exception:
            return 'decode_error'
        try:
            obj = json.loads(decoded)
        except (json.JSONDecodeError, ValueError):
            return f'non_json:{decoded[:_SUMMARY_TEXT_PREVIEW]!r}'
        return _summarize_entry_obj(obj)
    except OSError as exc:
        return f'oserror:{exc.__class__.__name__}'


def _summarize_entry_obj(obj: object) -> str:
    if not isinstance(obj, dict):
        return f'non_dict:{type(obj).__name__}'
    raw_entry_type = str(obj.get('type') or obj.get('entry_type') or '').strip()
    raw_payload = obj.get('payload') if isinstance(obj.get('payload'), dict) else {}
    raw_payload_type = str(
        (raw_payload.get('type') if raw_payload else None) or obj.get('payload_type') or ''
    ).strip()
    raw_turn_id = str(
        obj.get('turn_id')
        or (raw_payload.get('turn_id') if raw_payload else '')
        or ''
    ).strip()
    timestamp = str(obj.get('timestamp') or '').strip()
    try:
        normalized = extract_entry(obj)
    except Exception:
        normalized = None
    if normalized:
        role = str(normalized.get('role') or '').strip()
        text = normalized.get('text') or ''
        normalized_payload_type = str(normalized.get('payload_type') or '').strip()
    else:
        role = ''
        text = ''
        normalized_payload_type = ''
    text_preview = str(text)[:_SUMMARY_TEXT_PREVIEW]
    return (
        f'raw_entry_type={raw_entry_type!r} raw_payload_type={raw_payload_type!r} '
        f'normalized_role={role!r} normalized_payload_type={normalized_payload_type!r} '
        f'turn_id={raw_turn_id!r} timestamp={timestamp!r} text_preview={text_preview!r}'
    )


def reset_emitted_for_test() -> None:
    """Test helper. Resets the one-shot gate so tests can simulate restart."""
    _emitted.clear()


__all__ = ['maybe_emit_binding_diag', 'reset_emitted_for_test']
