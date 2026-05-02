from __future__ import annotations

from contextlib import contextmanager
from dataclasses import fields
from typing import Callable, Iterator

from provider_execution.common import request_anchor_from_runtime_state

from .models import CODEX_POLL_STATE_SCHEMA_VERSION, CodexPollState

PollStateMigration = Callable[[dict[str, object]], dict[str, object]]

_MIGRATIONS: dict[tuple[int, int], PollStateMigration] = {}


def _migrate_v1_to_v2(payload: dict[str, object]) -> dict[str, object]:
    migrated = dict(payload)
    migrated["schema_version"] = CODEX_POLL_STATE_SCHEMA_VERSION
    migrated.setdefault("requires_task_id", False)
    migrated.setdefault("task_id_probe_cache_key", None)
    return migrated


_MIGRATIONS[(1, CODEX_POLL_STATE_SCHEMA_VERSION)] = _migrate_v1_to_v2


def to_runtime_state(poll: CodexPollState) -> dict[str, object]:
    serialized: dict[str, object] = {}
    for field in fields(CodexPollState):
        value = getattr(poll, field.name)
        if field.name == "items":
            serialized[field.name] = []
        else:
            serialized[field.name] = value
    return serialized


def from_runtime_state(
    runtime_state: dict[str, object],
    *,
    fallback_request_anchor: str = "",
    target_schema_version: int | None = None,
) -> CodexPollState:
    payload = dict(runtime_state or {})
    if not payload.get("request_anchor"):
        payload["request_anchor"] = request_anchor_from_runtime_state(payload, fallback=fallback_request_anchor)

    source_version = int(payload.get("schema_version") or 1)
    if target_schema_version is None:
        target_schema_version = CODEX_POLL_STATE_SCHEMA_VERSION if source_version <= CODEX_POLL_STATE_SCHEMA_VERSION else source_version

    payload = _migrate_payload(payload, source_version=source_version, target_schema_version=target_schema_version)
    _validate_identity_requirements(payload, source_schema_version=source_version)
    return _poll_state_from_payload(payload)


def _migrate_payload(
    payload: dict[str, object],
    *,
    source_version: int,
    target_schema_version: int,
) -> dict[str, object]:
    current_version = source_version
    migrated = dict(payload)
    while current_version < target_schema_version:
        migration = _MIGRATIONS.get((current_version, current_version + 1))
        if migration is None:
            raise ValueError(f"missing Codex poll state migration: {current_version}->{current_version + 1}")
        migrated = migration(migrated)
        current_version += 1
        migrated["schema_version"] = current_version
    if current_version == target_schema_version and "schema_version" not in migrated:
        migrated["schema_version"] = current_version
    return migrated


def _validate_identity_requirements(payload: dict[str, object], *, source_schema_version: int) -> None:
    if not bool(payload.get("requires_task_id", False)):
        return
    if source_schema_version <= CODEX_POLL_STATE_SCHEMA_VERSION:
        return
    if str(payload.get("bound_task_id") or "").strip():
        return
    if str(payload.get("current_task_id") or "").strip():
        return
    raise ValueError("Codex poll state requires task identity but no task_id is present")


def _poll_state_from_payload(payload: dict[str, object]) -> CodexPollState:
    accepted = {field.name for field in fields(CodexPollState)}
    values = {name: payload[name] for name in accepted if name in payload}
    values["schema_version"] = int(values.get("schema_version") or CODEX_POLL_STATE_SCHEMA_VERSION)
    values["request_anchor"] = str(values.get("request_anchor") or "")
    values["next_seq"] = int(values.get("next_seq", 1) or 1)
    for name in (
        "anchor_seen",
        "current_turn_started",
        "bound_turn_started",
        "bound_turn_contaminated",
        "reached_terminal",
        "requires_task_id",
    ):
        values[name] = bool(values.get(name, False))
    for name in (
        "bound_turn_id",
        "bound_task_id",
        "reply_buffer",
        "last_agent_message",
        "last_final_answer",
        "last_assistant_message",
        "last_assistant_signature",
        "session_path",
        "current_turn_id",
        "current_task_id",
    ):
        values[name] = str(values.get(name) or "")
    if values.get("task_id_probe_cache_key") is not None:
        values["task_id_probe_cache_key"] = str(values.get("task_id_probe_cache_key") or "") or None
    values["items"] = []
    return CodexPollState(**values)


@contextmanager
def temporary_poll_state_migration(
    from_schema_version: int,
    to_schema_version: int,
    migration: PollStateMigration,
) -> Iterator[None]:
    key = (from_schema_version, to_schema_version)
    previous = _MIGRATIONS.get(key)
    _MIGRATIONS[key] = migration
    try:
        yield
    finally:
        if previous is None:
            _MIGRATIONS.pop(key, None)
        else:
            _MIGRATIONS[key] = previous


__all__ = [
    "from_runtime_state",
    "temporary_poll_state_migration",
    "to_runtime_state",
]
