from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from completion.models import CompletionItemKind
from provider_backends.codex.comm_runtime.log_entries import extract_entry
from provider_execution.base import ProviderSubmission
from provider_execution.common import build_item

from ..reply_logic import clean_codex_reply_text
from ..state_machine_runtime.models import CodexPollState
from .anchor_scan import (
    REPLAY_FAIL_NO_LOG,
    REPLAY_OK,
    ReplayResult,
    replay_anchor_bound,
)
from .quarantine import read_tail_lines, write_quarantine

# v8.4 Wave 2 Lane C — Issue #1 anchor-bound replay path. See
# docs/v8.4-plan.md §"Issue 1 — readback binding rehydration" for spec.
# Decision rule (i)/(ii)/(iii)/(iv): anchor present, assistant entry
# present, terminal entry present, reply_buffer empty for ≥N consecutive
# ticks. Default N=3, override via CCB_REPLAY_WEDGE_TICK_THRESHOLD.

_logger = logging.getLogger(__name__)

DEFAULT_WEDGE_TICK_THRESHOLD = 3


@dataclass
class RecoveryOutcome:
    action: str  # "noop", "deferred", "replayed", "abandoned"
    reason: str = ""
    turn_id: str = ""
    quarantine_jsonl: Path | None = None
    quarantine_manifest: Path | None = None


def is_wedge_condition(poll: CodexPollState) -> bool:
    """Same predicate the diag probe uses (binding_diag._is_wedge_condition).

    Kept as a module-local helper rather than imported to avoid the diag
    module's one-shot ``_emitted`` gate leaking into the replay path."""

    if poll.reached_terminal:
        return False
    if not poll.bound_turn_contaminated:
        return False
    if poll.reply_buffer:
        return False
    return True


def update_wedge_counter(poll: CodexPollState) -> None:
    if is_wedge_condition(poll):
        poll.consecutive_wedge_ticks += 1
    else:
        poll.consecutive_wedge_ticks = 0


def maybe_run_recovery(
    submission: ProviderSubmission,
    poll: CodexPollState,
    *,
    state: dict[str, object],
    now: str,
) -> RecoveryOutcome:
    """Run anchor-bound replay or abandon-with-quarantine when a binding
    contamination has persisted long enough to be unrecoverable on its own.

    Idempotent: returns ``noop`` when the wedge condition is not active or
    when the N-tick gate has not yet fired. ``replay_in_progress`` short-
    circuits a concurrent re-entry without mutating poll state.
    """

    update_wedge_counter(poll)

    if not is_wedge_condition(poll):
        if "consecutive_wedge_ticks" in state:
            _record_wedge_ticks(state, poll)
        return RecoveryOutcome(action="noop", reason="not_wedged")

    _record_wedge_ticks(state, poll)
    threshold = _resolve_threshold()
    if poll.consecutive_wedge_ticks < threshold:
        return RecoveryOutcome(
            action="deferred",
            reason=f"wedge_ticks={poll.consecutive_wedge_ticks}<threshold={threshold}",
        )

    if poll.replay_in_progress:
        return RecoveryOutcome(action="noop", reason="replay_in_progress")

    poll.replay_in_progress = True
    try:
        log_path = _log_path_from_state(state, poll=poll)
        result = replay_anchor_bound(log_path, request_anchor=poll.request_anchor)
        if result.success:
            _apply_replay_success(submission, poll, result=result, now=now)
            _record_wedge_ticks(state, poll)
            return RecoveryOutcome(
                action="replayed",
                reason=REPLAY_OK,
                turn_id=result.turn_id,
            )
        record = _abandon_with_quarantine(
            submission,
            poll,
            state=state,
            log_path=log_path,
            failure_reason=result.status,
            now=now,
        )
        outcome = RecoveryOutcome(
            action="abandoned",
            reason=result.status,
            turn_id=result.turn_id,
        )
        if record is not None:
            outcome.quarantine_jsonl = record[0]
            outcome.quarantine_manifest = record[1]
        _record_wedge_ticks(state, poll)
        return outcome
    finally:
        poll.replay_in_progress = False


def _apply_replay_success(
    submission: ProviderSubmission,
    poll: CodexPollState,
    *,
    result: ReplayResult,
    now: str,
) -> None:
    """Populate poll state with the replayed reply + emit the same
    ASSISTANT_CHUNK / TURN_BOUNDARY items the live path would have."""

    request_anchor = poll.request_anchor
    poll.bound_turn_id = result.turn_id or poll.bound_turn_id
    poll.current_turn_id = result.turn_id or poll.current_turn_id
    poll.bound_turn_contaminated = False
    poll.bound_turn_started = True
    poll.current_turn_started = True
    poll.anchor_seen = True

    for entry in result.assistant_entries:
        cleaned = clean_codex_reply_text(str(entry.get("text") or ""), request_anchor).strip()
        if not cleaned:
            continue
        poll.reply_buffer = _append_reply_text(poll.reply_buffer, cleaned)
        poll.last_assistant_message = cleaned
        phase = str(entry.get("phase") or "").strip().lower()
        if phase == "final_answer":
            poll.last_final_answer = cleaned
        payload: dict[str, object] = {
            "text": cleaned,
            "merged_text": poll.reply_buffer,
            "turn_id": result.turn_id,
            "replay": "anchor_bound",
        }
        if phase:
            payload["phase"] = phase
        if poll.session_path:
            payload["session_path"] = poll.session_path
        poll.items.append(
            build_item(
                submission,
                kind=CompletionItemKind.ASSISTANT_CHUNK,
                timestamp=now,
                seq=poll.next_seq,
                payload=payload,
            )
        )
        poll.next_seq += 1

    terminal = result.terminal_entry or {}
    payload_type = str(terminal.get("payload_type") or "").strip().lower()
    last_agent = str(terminal.get("last_agent_message") or "").strip()
    if last_agent:
        poll.last_agent_message = clean_codex_reply_text(last_agent, request_anchor).strip()

    if payload_type == "turn_aborted":
        poll.items.append(
            build_item(
                submission,
                kind=CompletionItemKind.TURN_ABORTED,
                timestamp=now,
                seq=poll.next_seq,
                payload={
                    "reason": "turn_aborted",
                    "status": "failed",
                    "last_agent_message": _selected_reply(poll),
                    "turn_id": result.turn_id,
                    "replay": "anchor_bound",
                    "session_path": poll.session_path,
                },
            )
        )
    else:
        poll.items.append(
            build_item(
                submission,
                kind=CompletionItemKind.TURN_BOUNDARY,
                timestamp=now,
                seq=poll.next_seq,
                payload={
                    "reason": "task_complete",
                    "last_agent_message": _selected_reply(poll),
                    "turn_id": result.turn_id,
                    "replay": "anchor_bound",
                    "session_path": poll.session_path,
                },
            )
        )
    poll.next_seq += 1
    poll.reached_terminal = True
    poll.consecutive_wedge_ticks = 0


def _abandon_with_quarantine(
    submission: ProviderSubmission,
    poll: CodexPollState,
    *,
    state: dict[str, object],
    log_path: Path | None,
    failure_reason: str,
    now: str,
) -> tuple[Path, Path] | None:
    """Quarantine the trailing session evidence and emit a TURN_ABORTED so
    the dispatcher unwedges the queue.

    Quarantine I/O failure is non-blocking: the abort item is always
    appended so no INCOMPLETE record is left behind."""

    tail_limit = _resolve_tail_entries_for_quarantine()
    raw_lines = read_tail_lines(log_path, tail_limit)
    inbound_event_id = str(submission.runtime_state.get("inbound_event_id") or "")
    record = write_quarantine(
        agent=getattr(submission, "agent_name", ""),
        job_id=getattr(submission, "job_id", ""),
        inbound_event_id=inbound_event_id,
        session_path=str(log_path) if log_path else (poll.session_path or None),
        offset=int(state.get("offset", 0) or 0),
        failure_reason=failure_reason,
        raw_lines=raw_lines,
        now=None,
    )

    poll.items.append(
        build_item(
            submission,
            kind=CompletionItemKind.TURN_ABORTED,
            timestamp=now,
            seq=poll.next_seq,
            payload={
                "reason": "replay_unrecoverable",
                "status": "failed",
                "replay_failure": failure_reason,
                "turn_id": poll.bound_turn_id,
                "session_path": poll.session_path,
                "quarantine_jsonl": str(record.jsonl_path) if record else None,
                "quarantine_manifest": str(record.manifest_path) if record else None,
                "last_agent_message": _selected_reply(poll),
            },
        )
    )
    poll.next_seq += 1
    poll.reached_terminal = True
    poll.bound_turn_contaminated = False
    poll.consecutive_wedge_ticks = 0
    if record is None:
        return None
    return record.jsonl_path, record.manifest_path


def _resolve_threshold() -> int:
    raw = os.environ.get("CCB_REPLAY_WEDGE_TICK_THRESHOLD", "").strip()
    if not raw:
        return DEFAULT_WEDGE_TICK_THRESHOLD
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_WEDGE_TICK_THRESHOLD
    return max(1, value)


def _resolve_tail_entries_for_quarantine() -> int:
    raw = os.environ.get("CCB_QUARANTINE_TAIL_ENTRIES", "").strip()
    if not raw:
        return 50
    try:
        value = int(raw)
    except ValueError:
        return 50
    return max(1, value)


def _record_wedge_ticks(state: dict[str, object], poll: CodexPollState) -> None:
    state["consecutive_wedge_ticks"] = poll.consecutive_wedge_ticks


def _log_path_from_state(state: dict[str, object], *, poll: CodexPollState) -> Path | None:
    raw = state.get("log_path") if isinstance(state, dict) else None
    if isinstance(raw, Path):
        return raw
    if raw:
        return Path(str(raw))
    if poll.session_path:
        return Path(poll.session_path)
    return None


def _append_reply_text(reply_buffer: str, cleaned: str) -> str:
    return f"{reply_buffer}\n{cleaned}".strip() if reply_buffer else cleaned


def _selected_reply(poll: CodexPollState) -> str:
    for candidate in (
        poll.last_agent_message,
        poll.last_final_answer,
        poll.last_assistant_message,
        poll.reply_buffer,
    ):
        text = str(candidate or "").strip()
        if text:
            return text
    return ""


# Re-export so callers don't have to know about the sub-modules.
__all__ = [
    "DEFAULT_WEDGE_TICK_THRESHOLD",
    "RecoveryOutcome",
    "is_wedge_condition",
    "maybe_run_recovery",
    "update_wedge_counter",
]


# Avoid an "unused" lint flag in CI for an import we deliberately keep
# warm in case downstream code wants to re-normalize quarantine rows.
_ = extract_entry
