from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Any, Iterable

from ccbd.state_mutation_guard import guarded_state_mutation_for_path
from message_bureau.reply_payloads import reply_id_from_payload

from .model_enums import InboundEventStatus
from .models import InboundEventRecord

MAX_MAILBOX_BYTES = 9_999_999
_TERMINAL_STATUSES = {
    InboundEventStatus.CONSUMED.value,
    InboundEventStatus.SUPERSEDED.value,
    InboundEventStatus.ABANDONED.value,
}


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
    max_pending_age_days: int = 7,
    max_bytes_per_mailbox: int = MAX_MAILBOX_BYTES,
) -> None:
    del now, max_pending_age_days
    if int(max_bytes_per_mailbox) > MAX_MAILBOX_BYTES:
        raise ValueError(f'max_bytes_per_mailbox cannot exceed {MAX_MAILBOX_BYTES}')

    preserved_inbound_rows: list[dict[str, Any]] = []
    reachable_reply_ids: set[str] = set()
    for agent_name in tuple(agent_names):
        path = layout.agent_inbox_path(agent_name)
        rows, corrupt_rows = _load_jsonl_objects(path)
        compacted = _compact_inbound_rows(rows)
        preserved_inbound_rows.extend(compacted)
        for row in compacted:
            if _is_terminal(row):
                continue
            reply_id = reply_id_from_payload(row.get('payload_ref'))
            if reply_id:
                reachable_reply_ids.add(reply_id)
        compact_jsonl_file_atomic(
            path,
            rows=compacted,
            cache_owner=cache_owner,
            cache_keys=(('inbound', str(path)),),
            corrupt_rows=corrupt_rows,
        )

    _compact_reachable_replies(layout, reachable_reply_ids, cache_owner=cache_owner)


def _compact_inbound_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    latest_by_id: dict[str, dict[str, Any]] = {}
    first_seen_order: list[str] = []
    for row in rows:
        event_id = str(row.get('inbound_event_id') or '').strip()
        if not event_id:
            continue
        if event_id not in latest_by_id:
            first_seen_order.append(event_id)
        latest_by_id[event_id] = row
    return [latest_by_id[event_id] for event_id in first_seen_order]


def _is_terminal(row: dict[str, Any]) -> bool:
    return str(row.get('status') or '').strip() in _TERMINAL_STATUSES


def _compact_reachable_replies(layout, reachable_reply_ids: set[str], *, cache_owner=None) -> None:
    path = getattr(layout, 'ccbd_replies_path', None)
    if path is None:
        return
    path = Path(path)
    if not path.exists():
        return
    rows, corrupt_rows = _load_jsonl_objects(path)
    latest_by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in rows:
        reply_id = str(row.get('reply_id') or '').strip()
        if not reply_id:
            continue
        if reply_id not in latest_by_id:
            order.append(reply_id)
        latest_by_id[reply_id] = row
    # Keep all latest reply rows for now and make reachability an invariant,
    # not a deletion filter. The body store is still used for historical
    # `ccb pend <job_id>` lookup after a head has been acked.
    compacted = [latest_by_id[reply_id] for reply_id in order]
    compact_jsonl_file_atomic(
        path,
        rows=compacted,
        cache_owner=cache_owner,
        cache_keys=(('replies', str(path)),),
        corrupt_rows=corrupt_rows,
    )


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
    'compact_jsonl_file_atomic',
    'compact_mailbox_jsonl',
]
