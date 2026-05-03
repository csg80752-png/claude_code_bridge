from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

_logger = logging.getLogger(__name__)

# v8.3.2 keeps v1 rows readable during mixed burn-in while new rows use v3.
BODY_READ_FOLLOWUP_SCHEMA_V1 = 1
BODY_READ_FOLLOWUP_SCHEMA_V3 = 3
BODY_READ_FOLLOWUP_CURRENT_SCHEMA = BODY_READ_FOLLOWUP_SCHEMA_V3
BODY_READ_FOLLOWUP_CYCLE = 'v8.3.2'

_METRICS_SUBDIR = ('metrics',)
_METRICS_FILE = 'body_read_followup.jsonl'


def metrics_path(project_root: Path) -> Path:
    return Path(project_root) / '.ccb' / _METRICS_SUBDIR[0] / _METRICS_FILE


def read_body_read_followup_records(project_root: Path) -> list[dict]:
    """Read supported body-read telemetry and quarantine incompatible rows."""
    path = metrics_path(Path(project_root))
    if not path.exists():
        return []

    records: list[dict] = []
    future_rows: list[str] = []
    corrupt_rows: list[str] = []

    try:
        with path.open('r', encoding='utf-8') as handle:
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
                schema_version = payload.get('schema_version')
                if schema_version in {BODY_READ_FOLLOWUP_SCHEMA_V1, BODY_READ_FOLLOWUP_SCHEMA_V3}:
                    records.append(payload)
                    continue
                future_rows.append(raw_line)
    except OSError:
        _logger.debug('cmd-delivery telemetry read failed', exc_info=True)
        return records

    _write_quarantine(path.with_name('body_read_followup.future-schema.jsonl'), future_rows)
    _write_quarantine(path.with_name('body_read_followup.corrupt.jsonl'), corrupt_rows)
    return records


def _append_record(project_root: Optional[Path], record: dict) -> None:
    if project_root is None:
        return
    try:
        target = metrics_path(Path(project_root))
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + '\n')
    except Exception:
        _logger.debug('cmd-delivery telemetry write failed', exc_info=True)


def _write_quarantine(path: Path, rows: list[str]) -> None:
    if not rows:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as handle:
            for row in rows:
                handle.write(row if row.endswith('\n') else row + '\n')
    except OSError:
        _logger.debug('cmd-delivery telemetry quarantine write failed', exc_info=True)


def _v3_record(event: str, **fields) -> dict:
    return {
        'schema_version': BODY_READ_FOLLOWUP_CURRENT_SCHEMA,
        'cycle': BODY_READ_FOLLOWUP_CYCLE,
        'event': event,
        **fields,
    }


def record_cmd_delivery_header_inject_success(
    project_root: Optional[Path],
    *,
    reply_id: str,
    foreground_command: str,
    delivered_at: str,
    body_char_count: int,
    delivery_mode: str = 'header_only',
    header_only_compatible: bool = True,
) -> None:
    _append_record(project_root, _v3_record(
        'cmd_delivery_header_inject_success',
        reply_id=reply_id,
        foreground_command=foreground_command,
        body_char_count=body_char_count,
        delivered_at=delivered_at,
        delivery_mode=delivery_mode,
        header_only_compatible=header_only_compatible,
    ))


def record_cmd_delivery_header_inject_error(
    project_root: Optional[Path],
    *,
    reply_id: str,
    foreground_command: str,
    failed_at: str,
    body_char_count: int,
    reason: str,
    delivery_mode: str,
    header_only_compatible: bool,
) -> None:
    _append_record(project_root, _v3_record(
        'cmd_delivery_header_inject_error',
        reply_id=reply_id,
        foreground_command=foreground_command,
        body_char_count=body_char_count,
        failed_at=failed_at,
        reason=reason,
        delivery_mode=delivery_mode,
        header_only_compatible=bool(header_only_compatible),
    ))


def record_cmd_delivery_mode_invalid(
    project_root: Optional[Path],
    *,
    env_name: str,
    raw_value: str,
    reason: str,
) -> None:
    _append_record(project_root, _v3_record(
        'cmd_delivery_mode_invalid',
        env_name=env_name,
        raw_value=raw_value,
        reason=reason,
        fallback_mode='full_body',
    ))


def record_cmd_delivery_legacy_env_seen(
    project_root: Optional[Path],
    *,
    ignored: bool,
    raw_value: str,
    reason: str,
) -> None:
    _append_record(project_root, _v3_record(
        'cmd_delivery_legacy_env_seen',
        env_name='CCB_HEADER_ONLY',
        ignored=bool(ignored),
        raw_value=raw_value,
        reason=reason,
    ))


def record_cmd_delivery_header_too_large(
    project_root: Optional[Path],
    *,
    reply_id: str,
    body_char_count: int,
    body_byte_count: int,
) -> None:
    _append_record(project_root, _v3_record(
        'cmd_delivery_header_too_large',
        reply_id=reply_id,
        body_char_count=body_char_count,
        body_byte_count=body_byte_count,
        max_body_byte_count=9_999_999,
    ))


def record_header_only_dispatch(
    project_root: Path,
    *,
    reply_id: str,
    body_file: Path,
    dispatched_at: str,
    body_char_count: int,
) -> None:
    # Kept for v8.3.1 reader/test compatibility. v8.3.2 header-only no longer
    # writes external body pointers, but historical callers may still import it.
    _append_record(project_root, _v3_record(
        'header_only_dispatch',
        reply_id=reply_id,
        body_file=str(body_file),
        dispatched_at=dispatched_at,
        body_char_count=body_char_count,
    ))


def record_long_reply_fallback(
    project_root: Optional[Path],
    *,
    reply_id: str,
    reason: str,
    body_char_count: int,
    dispatched_at: str,
) -> None:
    _append_record(project_root, _v3_record(
        'long_reply_fell_back_full_body',
        reply_id=reply_id,
        reason=reason,
        body_char_count=body_char_count,
        dispatched_at=dispatched_at,
    ))


def record_phase2_failure(
    project_root: Optional[Path],
    *,
    reply_id: str,
    stage: str,
    reason: str,
    body_char_count: int,
    failed_at: str,
    foreground_command: str,
    pane_alive: bool,
    cached: bool,
) -> None:
    _append_record(project_root, _v3_record(
        'cmd_phase2_failure',
        reply_id=reply_id,
        stage=stage,
        reason=reason,
        body_char_count=body_char_count,
        failed_at=failed_at,
        foreground_command=foreground_command,
        pane_alive=bool(pane_alive),
        cached=bool(cached),
    ))


def record_cmd_delivery_success(
    project_root: Optional[Path],
    *,
    reply_id: str,
    foreground_command: str,
    delivered_at: str,
    body_char_count: int,
) -> None:
    _append_record(project_root, _v3_record(
        'cmd_delivery_success',
        reply_id=reply_id,
        foreground_command=foreground_command,
        body_char_count=body_char_count,
        delivered_at=delivered_at,
    ))


def record_cmd_delivery_held(
    project_root: Optional[Path],
    *,
    reply_id: str,
    foreground_command: str,
    held_at: str,
    body_char_count: int,
    held_reason: str = 'not_safe_consumer',
    delivery_mode: str = 'full_body',
    header_only_compatible: bool = False,
) -> None:
    _append_record(project_root, _v3_record(
        'cmd_delivery_held',
        reply_id=reply_id,
        foreground_command=foreground_command,
        held_reason=held_reason,
        body_char_count=body_char_count,
        held_at=held_at,
        delivery_mode=delivery_mode,
        header_only_compatible=bool(header_only_compatible),
    ))


__all__ = [
    'BODY_READ_FOLLOWUP_CURRENT_SCHEMA',
    'BODY_READ_FOLLOWUP_SCHEMA_V1',
    'BODY_READ_FOLLOWUP_SCHEMA_V3',
    'metrics_path',
    'read_body_read_followup_records',
    'record_cmd_delivery_header_inject_error',
    'record_cmd_delivery_header_inject_success',
    'record_cmd_delivery_header_too_large',
    'record_cmd_delivery_held',
    'record_cmd_delivery_legacy_env_seen',
    'record_cmd_delivery_mode_invalid',
    'record_cmd_delivery_success',
    'record_header_only_dispatch',
    'record_long_reply_fallback',
    'record_phase2_failure',
]
