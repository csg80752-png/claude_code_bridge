from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import time
from typing import Any, Iterable

from ccbd.state_mutation_guard import guarded_state_mutation_for_path
from message_bureau.reply_payloads import reply_id_from_payload

from .model_enums import InboundEventStatus
from .models import InboundEventRecord

MAX_MAILBOX_BYTES = 9_999_999
DEFAULT_MAX_PENDING_AGE_DAYS = 7
_logger = logging.getLogger(__name__)
_TERMINAL_STATUSES = {
    InboundEventStatus.CONSUMED.value,
    InboundEventStatus.SUPERSEDED.value,
    InboundEventStatus.ABANDONED.value,
}


@dataclass(frozen=True)
class MailboxGcOptions:
    max_pending_age_days: int
    max_bytes_per_mailbox: int


def resolve_mailbox_gc_options(
    *,
    default_max_age_days: int = DEFAULT_MAX_PENDING_AGE_DAYS,
    default_max_bytes: int = MAX_MAILBOX_BYTES,
) -> MailboxGcOptions:
    age_days = _int_env('CCB_MAILBOX_MAX_AGE_DAYS', default_max_age_days, minimum=0)
    max_bytes = _int_env('CCB_MAILBOX_MAX_BYTES', default_max_bytes, minimum=1)
    return MailboxGcOptions(
        max_pending_age_days=age_days,
        max_bytes_per_mailbox=min(max_bytes, MAX_MAILBOX_BYTES),
    )


def compact_jsonl_file_atomic(
    path: Path,
    *,
    rows: Iterable[dict[str, Any]],
    cache_owner=None,
    cache_keys: Iterable[Any] = (),
    corrupt_rows: Iterable[str] = (),
) -> None:
    """Rewrite a JSONL file through temp+fsync+atomic rename under the guard."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_name(f'{target.name}.tmp.{os.getpid()}.{time.time_ns()}')
    payload_rows = tuple(dict(row) for row in rows)
    corrupt_payloads = tuple(corrupt_rows)

    with guarded_state_mutation_for_path(target, owner='mailbox.gc', blocking=True):
        try:
            with tmp_path.open('w', encoding='utf-8') as handle:
                for row in payload_rows:
                    handle.write(json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n')
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, target)
            _fsync_parent(target)
            _quarantine_corrupt_rows(target, corrupt_payloads)
        finally:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass

    _invalidate_jsonl_caches(cache_owner, cache_keys)


def compact_mailbox_jsonl(
    layout,
    *,
    agent_names: Iterable[str],
    now: str,
    cache_owner=None,
    reply_cache_owner=None,
    max_pending_age_days: int = DEFAULT_MAX_PENDING_AGE_DAYS,
    max_bytes_per_mailbox: int = MAX_MAILBOX_BYTES,
) -> None:
    options = resolve_mailbox_gc_options(
        default_max_age_days=max_pending_age_days,
        default_max_bytes=max_bytes_per_mailbox,
    )
    now_dt = _parse_timestamp(now) or datetime.now(timezone.utc)

    reachable_reply_ids: set[str] = set()
    for agent_name in tuple(agent_names):
        path = layout.agent_inbox_path(agent_name)
        compacted = _compact_jsonl_file_with_locked_transform(
            path,
            transform=lambda rows: _compact_inbound_rows(rows, now_dt=now_dt, options=options),
            cache_owner=cache_owner,
            cache_keys=(('inbound', str(path)),),
        )
        for row in compacted:
            if not _is_terminal(row):
                reply_id = reply_id_from_payload(row.get('payload_ref'))
                if reply_id:
                    reachable_reply_ids.add(reply_id)

    _compact_reachable_replies(layout, reachable_reply_ids, cache_owner=reply_cache_owner, options=options)


def _compact_inbound_rows(
    rows: Iterable[dict[str, Any]],
    *,
    now_dt: datetime,
    options: MailboxGcOptions,
) -> list[dict[str, Any]]:
    latest_by_id: dict[str, dict[str, Any]] = {}
    first_seen_order: list[str] = []
    for row in rows:
        event_id = str(row.get('inbound_event_id') or '').strip()
        if not event_id:
            continue
        if event_id not in latest_by_id:
            first_seen_order.append(event_id)
        latest_by_id[event_id] = row
    compacted = [latest_by_id[event_id] for event_id in first_seen_order]
    cutoff = now_dt - timedelta(days=options.max_pending_age_days)
    age_filtered = [
        row
        for row in compacted
        if not _is_terminal(row) or _row_timestamp(row) >= cutoff
    ]
    return _enforce_jsonl_size_limit(
        age_filtered,
        protected=lambda row: not _is_terminal(row),
        max_bytes=options.max_bytes_per_mailbox,
    )


def _is_terminal(row: dict[str, Any]) -> bool:
    return str(row.get('status') or '').strip() in _TERMINAL_STATUSES


def _compact_reachable_replies(
    layout,
    reachable_reply_ids: set[str],
    *,
    cache_owner=None,
    options: MailboxGcOptions,
) -> None:
    path = getattr(layout, 'ccbd_replies_path', None)
    if path is None:
        return
    path = Path(path)
    if not path.exists():
        return
    _compact_jsonl_file_with_locked_transform(
        path,
        transform=lambda rows: _compact_reply_rows(rows, reachable_reply_ids, options=options),
        cache_owner=cache_owner,
        cache_keys=(('replies', str(path)),),
    )


def _compact_reply_rows(
    rows: Iterable[dict[str, Any]],
    reachable_reply_ids: set[str],
    *,
    options: MailboxGcOptions,
) -> list[dict[str, Any]]:
    latest_by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in rows:
        reply_id = str(row.get('reply_id') or '').strip()
        if not reply_id:
            continue
        if reply_id not in latest_by_id:
            order.append(reply_id)
        latest_by_id[reply_id] = row
    compacted = [latest_by_id[reply_id] for reply_id in order]
    return _enforce_jsonl_size_limit(
        compacted,
        protected=lambda row: str(row.get('reply_id') or '').strip() in reachable_reply_ids,
        max_bytes=options.max_bytes_per_mailbox,
    )


def _compact_jsonl_file_with_locked_transform(
    path: Path,
    *,
    transform,
    cache_owner=None,
    cache_keys: Iterable[Any] = (),
) -> list[dict[str, Any]]:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_name(f'{target.name}.tmp.{os.getpid()}.{time.time_ns()}')
    with guarded_state_mutation_for_path(target, owner='mailbox.gc', blocking=True):
        try:
            rows, corrupt_rows = _load_jsonl_objects(target)
            compacted = list(transform(rows))
            with tmp_path.open('w', encoding='utf-8') as handle:
                for row in compacted:
                    handle.write(json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n')
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, target)
            _fsync_parent(target)
            _quarantine_corrupt_rows(target, corrupt_rows)
        finally:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass
    _invalidate_jsonl_caches(cache_owner, cache_keys)
    return compacted


def _load_jsonl_objects(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    target = Path(path)
    if not target.exists():
        return [], []
    rows: list[dict[str, Any]] = []
    corrupt_rows: list[str] = []
    with target.open('r', encoding='utf-8') as handle:
        for raw_line in handle:
            text = raw_line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                corrupt_rows.append(raw_line)
                continue
            if not isinstance(payload, dict):
                corrupt_rows.append(raw_line)
                continue
            if payload.get('record_type') == 'inbound_event_record':
                try:
                    InboundEventRecord.from_record(payload)
                except Exception:
                    corrupt_rows.append(raw_line)
                    continue
            rows.append(payload)
    return rows, corrupt_rows


def _quarantine_corrupt_rows(path: Path, corrupt_rows: Iterable[str]) -> None:
    rows = tuple(corrupt_rows)
    if not rows:
        return
    quarantine = path.with_name(f'{path.name}.corrupt.{time.time_ns()}.jsonl')
    with quarantine.open('w', encoding='utf-8') as handle:
        for row in rows:
            handle.write(row if row.endswith('\n') else row + '\n')
        handle.flush()
        os.fsync(handle.fileno())
    _fsync_parent(quarantine)


def _enforce_jsonl_size_limit(
    rows: list[dict[str, Any]],
    *,
    protected,
    max_bytes: int,
) -> list[dict[str, Any]]:
    sizes = [_jsonl_row_size(row) for row in rows]
    total = sum(sizes)
    if total <= max_bytes:
        return rows
    keep = [True] * len(rows)
    for index, row in enumerate(rows):
        if total <= max_bytes:
            break
        if protected(row):
            continue
        keep[index] = False
        total -= sizes[index]
    return [row for row, keep_row in zip(rows, keep) if keep_row]


def _jsonl_row_size(row: dict[str, Any]) -> int:
    return len(json.dumps(row, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) + 1


def _row_timestamp(row: dict[str, Any]) -> datetime:
    for key in ('finished_at', 'created_at'):
        parsed = _parse_timestamp(str(row.get(key) or ''))
        if parsed is not None:
            return parsed
    return datetime.now(timezone.utc)


def _parse_timestamp(value: str) -> datetime | None:
    text = str(value or '').strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _int_env(name: str, default: int, *, minimum: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return int(default)
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        _logger.debug('invalid mailbox gc env %s=%r; using default %s', name, raw, default)
        return int(default)
    if value < minimum:
        _logger.debug('invalid mailbox gc env %s=%r below minimum %s; using default %s', name, raw, minimum, default)
        return int(default)
    return value


def _invalidate_jsonl_caches(cache_owner, cache_keys: Iterable[Any]) -> None:
    invalidate = getattr(cache_owner, 'invalidate', None)
    if not callable(invalidate):
        return
    for key in cache_keys:
        invalidate(key)


def _fsync_parent(path: Path) -> None:
    try:
        fd = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


__all__ = [
    'MAX_MAILBOX_BYTES',
    'MailboxGcOptions',
    'compact_jsonl_file_atomic',
    'compact_mailbox_jsonl',
    'resolve_mailbox_gc_options',
]
