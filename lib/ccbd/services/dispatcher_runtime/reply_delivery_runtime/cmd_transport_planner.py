from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import logging
import os
from pathlib import Path
import re
from typing import Optional

from .formatting import format_reply_delivery_body
from .cmd_header_compatibility import validate_cmd_header_only_compatibility_marker

_logger = logging.getLogger(__name__)

_BODY_CHAR_THRESHOLD = 1500  # kept for compatibility with older tests/imports
MAX_CMD_HEADER_LEN = 100
MAX_CMD_HEADER_BYTES_FIELD = 9_999_999
CMD_HEADER_RE = re.compile(
    r'^\[CCB\] job=(job_[0-9a-f]{8,16}|target=cmd) '
    r'from=[a-z][a-z0-9_-]{0,31} '
    r'bytes=(0|[1-9][0-9]{0,6}) pend=ccb-pend$'
)
_JOB_ID_RE = re.compile(r'^job_[0-9a-f]{8,16}$')
_SENDER_RE = re.compile(r'^[a-z][a-z0-9_-]{0,31}$')
_FORBIDDEN_HEADER_CHARS = frozenset("`$\\'\";|><(){}")
_NEW_MODE_ENV = 'CCB_CMD_DELIVERY_MODE'
_LEGACY_MODE_ENV = 'CCB_HEADER_ONLY'
_LEGACY_TRUTHY = frozenset({'1', 'true', 'yes', 'on'})
_LEGACY_FALSY = frozenset({'0', 'false', 'no', 'off'})


class CmdHeaderValidationError(ValueError):
    pass


class CmdDeliveryMode(str, Enum):
    HEADER_ONLY = 'header_only'
    FULL_BODY = 'full_body'


@dataclass(frozen=True)
class CmdDeliveryModeResult:
    mode: CmdDeliveryMode
    reason: str
    raw_value: str | None = None
    legacy_raw_value: str | None = None
    header_only_compatible: bool = False


@dataclass(frozen=True)
class CmdDeliveryPlan:
    body: str
    header_only: bool
    body_file: Path | None


@dataclass(frozen=True)
class CmdDeliveryFallback:
    reason: str
    body_char_count: int


def beta_header_only_allowed(*, smoke_passed: bool) -> bool:
    return bool(smoke_passed)


def resolve_cmd_delivery_mode(
    *,
    project_root: Optional[Path],
    header_only_compatible: bool | None = None,
) -> CmdDeliveryModeResult:
    from .cmd_delivery_telemetry import (
        record_cmd_delivery_legacy_env_seen,
        record_cmd_delivery_mode_invalid,
    )

    raw = os.environ.get(_NEW_MODE_ENV)
    legacy = os.environ.get(_LEGACY_MODE_ENV)
    if raw is not None:
        normalized = str(raw).strip().lower()
        if legacy is not None:
            record_cmd_delivery_legacy_env_seen(
                project_root,
                ignored=True,
                raw_value=str(legacy),
                reason='new_env_precedence',
            )
        if normalized == CmdDeliveryMode.HEADER_ONLY.value:
            compatible = _resolve_header_only_compatible(
                project_root=project_root,
                header_only_compatible=header_only_compatible,
                requested_mode=CmdDeliveryMode.HEADER_ONLY,
            )
            return CmdDeliveryModeResult(
                CmdDeliveryMode.HEADER_ONLY,
                'explicit',
                raw_value=str(raw),
                legacy_raw_value=legacy,
                header_only_compatible=compatible,
            )
        if normalized == CmdDeliveryMode.FULL_BODY.value:
            compatible = _resolve_header_only_compatible(
                project_root=project_root,
                header_only_compatible=header_only_compatible,
                requested_mode=CmdDeliveryMode.FULL_BODY,
            )
            return CmdDeliveryModeResult(
                CmdDeliveryMode.FULL_BODY,
                'explicit',
                raw_value=str(raw),
                legacy_raw_value=legacy,
                header_only_compatible=compatible,
            )
        record_cmd_delivery_mode_invalid(
            project_root,
            env_name=_NEW_MODE_ENV,
            raw_value=str(raw),
            reason='invalid',
        )
        return CmdDeliveryModeResult(
            CmdDeliveryMode.FULL_BODY,
            'invalid',
            raw_value=str(raw),
            legacy_raw_value=legacy,
            header_only_compatible=_resolve_header_only_compatible(
                project_root=project_root,
                header_only_compatible=header_only_compatible,
                requested_mode=CmdDeliveryMode.FULL_BODY,
            ),
        )

    if legacy is not None:
        normalized = str(legacy).strip().lower()
        record_cmd_delivery_legacy_env_seen(
            project_root,
            ignored=False,
            raw_value=str(legacy),
            reason='legacy_only',
        )
        if normalized in _LEGACY_TRUTHY:
            compatible = _resolve_header_only_compatible(
                project_root=project_root,
                header_only_compatible=header_only_compatible,
                requested_mode=CmdDeliveryMode.HEADER_ONLY,
            )
            return CmdDeliveryModeResult(
                CmdDeliveryMode.HEADER_ONLY,
                'legacy',
                legacy_raw_value=str(legacy),
                header_only_compatible=compatible,
            )
        if normalized in _LEGACY_FALSY:
            compatible = _resolve_header_only_compatible(
                project_root=project_root,
                header_only_compatible=header_only_compatible,
                requested_mode=CmdDeliveryMode.FULL_BODY,
            )
            return CmdDeliveryModeResult(
                CmdDeliveryMode.FULL_BODY,
                'legacy',
                legacy_raw_value=str(legacy),
                header_only_compatible=compatible,
            )
        record_cmd_delivery_mode_invalid(
            project_root,
            env_name=_LEGACY_MODE_ENV,
            raw_value=str(legacy),
            reason='invalid_legacy',
        )
        return CmdDeliveryModeResult(
            CmdDeliveryMode.FULL_BODY,
            'invalid_legacy',
            legacy_raw_value=str(legacy),
            header_only_compatible=_resolve_header_only_compatible(
                project_root=project_root,
                header_only_compatible=header_only_compatible,
                requested_mode=CmdDeliveryMode.FULL_BODY,
            ),
        )

    return CmdDeliveryModeResult(
        CmdDeliveryMode.FULL_BODY,
        'default_beta_full_body',
        header_only_compatible=_resolve_header_only_compatible(
            project_root=project_root,
            header_only_compatible=header_only_compatible,
            requested_mode=CmdDeliveryMode.FULL_BODY,
        ),
    )


def header_only_enabled(project_root: Optional[Path] = None) -> bool:
    return effective_cmd_delivery_mode(resolve_cmd_delivery_mode(project_root=project_root)) is CmdDeliveryMode.HEADER_ONLY


def effective_cmd_delivery_mode(result: CmdDeliveryModeResult) -> CmdDeliveryMode:
    if result.mode is CmdDeliveryMode.HEADER_ONLY and not result.header_only_compatible:
        return CmdDeliveryMode.FULL_BODY
    return result.mode


def parse_cmd_header_tokens(header: str) -> dict[str, str]:
    if not CMD_HEADER_RE.match(header):
        raise CmdHeaderValidationError('invalid cmd header')
    tokens = header.split()
    if len(tokens) != 5 or tokens[0] != '[CCB]':
        raise CmdHeaderValidationError('invalid cmd header token count')
    parsed: dict[str, str] = {}
    for token in tokens[1:]:
        if '=' not in token:
            raise CmdHeaderValidationError(f'invalid cmd header token: {token}')
        key, value = token.split('=', 1)
        if key in parsed:
            raise CmdHeaderValidationError(f'duplicate cmd header token: {key}')
        parsed[key] = value
    if set(parsed) != {'job', 'from', 'bytes', 'pend'}:
        raise CmdHeaderValidationError('invalid cmd header keys')
    return parsed


def prepare_cmd_payload(*, sender_id: str, body_bytes: int, source_job_id: str | None) -> str:
    sender = _validated_sender(sender_id)
    body_size = _validated_body_bytes(body_bytes)
    job_target = _validated_job_or_fallback(source_job_id)
    header = f'[CCB] job={job_target} from={sender} bytes={body_size} pend=ccb-pend'
    _validate_header_text(header)
    return header


def plan_cmd_delivery(
    dispatcher,
    reply,
    *,
    project_root: Optional[Path],
    body_store,
    delivery_mode_result: CmdDeliveryModeResult | None = None,
) -> tuple[CmdDeliveryPlan, Optional[CmdDeliveryFallback]]:
    del body_store
    mode = delivery_mode_result or CmdDeliveryModeResult(CmdDeliveryMode.FULL_BODY, 'default_beta_full_body')
    if effective_cmd_delivery_mode(mode) is CmdDeliveryMode.FULL_BODY or _is_heartbeat(reply):
        return CmdDeliveryPlan(body=format_reply_delivery_body(dispatcher, reply), header_only=False, body_file=None), None

    raw_body = str(reply.reply or '')
    try:
        header = prepare_cmd_payload(
            sender_id=str(getattr(reply, 'agent_name', '') or ''),
            body_bytes=len(raw_body.encode('utf-8')),
            source_job_id=_source_job_id(dispatcher, reply),
        )
    except CmdHeaderValidationError as exc:
        from .cmd_delivery_telemetry import record_cmd_delivery_header_too_large

        if 'body bytes' in str(exc):
            record_cmd_delivery_header_too_large(
                project_root,
                reply_id=str(getattr(reply, 'reply_id', '') or ''),
                body_char_count=len(raw_body),
                body_byte_count=len(raw_body.encode('utf-8')),
            )
        raise
    return CmdDeliveryPlan(body=header, header_only=True, body_file=None), None


def _validated_sender(value: str) -> str:
    text = str(value or '').strip()
    if not _SENDER_RE.fullmatch(text):
        raise CmdHeaderValidationError(f'invalid sender: {value!r}')
    return text


def _validated_body_bytes(value: int) -> int:
    try:
        number = int(value)
    except Exception as exc:
        raise CmdHeaderValidationError(f'invalid body bytes: {value!r}') from exc
    if number < 0 or number > MAX_CMD_HEADER_BYTES_FIELD:
        raise CmdHeaderValidationError(f'body bytes out of range: {number}')
    return number


def _validated_job_or_fallback(value: str | None) -> str:
    text = str(value or '').strip()
    if _JOB_ID_RE.fullmatch(text):
        return text
    return 'target=cmd'


def _resolve_header_only_compatible(
    *,
    project_root: Optional[Path],
    header_only_compatible: bool | None,
    requested_mode: CmdDeliveryMode,
) -> bool:
    if header_only_compatible is not None:
        return bool(header_only_compatible)
    if requested_mode is not CmdDeliveryMode.HEADER_ONLY:
        return False
    return validate_cmd_header_only_compatibility_marker(project_root).compatible


def _validate_header_text(header: str) -> None:
    try:
        header.encode('ascii')
    except UnicodeEncodeError as exc:
        raise CmdHeaderValidationError('cmd header must be ASCII') from exc
    if any(char in header for char in _FORBIDDEN_HEADER_CHARS):
        raise CmdHeaderValidationError('cmd header contains forbidden shell metacharacter')
    if '\n' in header or '\r' in header or '\x1b' in header:
        raise CmdHeaderValidationError('cmd header contains control characters')
    if len(header) > MAX_CMD_HEADER_LEN:
        raise CmdHeaderValidationError(f'cmd header exceeds {MAX_CMD_HEADER_LEN} chars')
    if not CMD_HEADER_RE.fullmatch(header):
        raise CmdHeaderValidationError(f'invalid cmd header: {header!r}')


def _is_heartbeat(reply) -> bool:
    diagnostics = getattr(reply, 'diagnostics', {}) or {}
    return str(diagnostics.get('notice_kind') or '').strip().lower() == 'heartbeat'


def _source_job_id(dispatcher, reply) -> str | None:
    source_job = _source_job(dispatcher, reply)
    if source_job is None:
        return None
    return str(getattr(source_job, 'job_id', '') or '').strip() or None


def _source_job(dispatcher, reply):
    try:
        attempt_store = dispatcher._message_bureau_control._attempt_store
    except AttributeError:
        return None
    attempt = attempt_store.get_latest(reply.attempt_id)
    if attempt is None:
        return None
    if hasattr(dispatcher, 'get_job'):
        return dispatcher.get_job(attempt.job_id)
    try:
        from ..records import get_job
    except ImportError:
        return None
    return get_job(dispatcher, attempt.job_id)


__all__ = [
    'CMD_HEADER_RE',
    'MAX_CMD_HEADER_BYTES_FIELD',
    'MAX_CMD_HEADER_LEN',
    'CmdDeliveryFallback',
    'CmdDeliveryMode',
    'CmdDeliveryModeResult',
    'CmdDeliveryPlan',
    'CmdHeaderValidationError',
    'beta_header_only_allowed',
    'effective_cmd_delivery_mode',
    'header_only_enabled',
    'parse_cmd_header_tokens',
    'plan_cmd_delivery',
    'prepare_cmd_payload',
    'resolve_cmd_delivery_mode',
]
