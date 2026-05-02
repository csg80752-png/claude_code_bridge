from __future__ import annotations

from dataclasses import fields
import json
from pathlib import Path

import pytest

from completion.models import CompletionSourceKind
from provider_backends.codex.execution_runtime.state_machine_runtime.finalization import (
    finalize_poll_result,
)
from provider_backends.codex.execution_runtime.state_machine_runtime.models import (
    CODEX_POLL_STATE_SCHEMA_VERSION,
    CodexPollState,
    build_poll_state,
)
from provider_backends.codex.execution_runtime.state_machine_runtime import serialization as serialization_module
from provider_backends.codex.execution_runtime.state_machine_runtime.serialization import (
    from_runtime_state,
    temporary_poll_state_migration,
    to_runtime_state,
)
from provider_execution.base import ProviderSubmission


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "codex_poll_state_v2"


def _submission(runtime_state: dict[str, object] | None = None) -> ProviderSubmission:
    return ProviderSubmission(
        job_id="job_serialize",
        agent_name="agent1",
        provider="codex",
        accepted_at="2026-05-02T00:00:00Z",
        ready_at="2026-05-02T00:00:00Z",
        source_kind=CompletionSourceKind.PROTOCOL_EVENT_STREAM,
        reply="",
        runtime_state={"state": {}, **(runtime_state or {})},
    )


def _v1_runtime_state() -> dict[str, object]:
    return {
        "request_anchor": "job_serialize",
        "next_seq": 7,
        "anchor_seen": True,
        "bound_turn_id": "turn-1",
        "reply_buffer": "buffer",
        "last_agent_message": "agent",
        "last_final_answer": "final",
        "last_assistant_message": "assistant",
        "last_assistant_signature": "sig",
        "session_path": "/tmp/session.jsonl",
        "current_turn_id": "turn-1",
        "current_turn_started": True,
        "bound_turn_started": True,
        "bound_turn_contaminated": False,
        "items": [],
        "reached_terminal": False,
    }


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_codex_poll_state_exports_schema_version() -> None:
    poll = CodexPollState(request_anchor="job_serialize")

    serialized = to_runtime_state(poll)

    assert serialized["schema_version"] == CODEX_POLL_STATE_SCHEMA_VERSION
    assert CODEX_POLL_STATE_SCHEMA_VERSION == 3


def test_codex_poll_state_imports_v1_without_schema_version() -> None:
    poll = from_runtime_state(_v1_runtime_state())

    assert poll.schema_version == CODEX_POLL_STATE_SCHEMA_VERSION
    assert poll.request_anchor == "job_serialize"
    assert poll.bound_turn_id == "turn-1"
    assert poll.requires_turn_id is False
    assert poll.turn_id_probe_cache_key is None


def test_codex_poll_state_serialized_fields_match_dataclass_fields_after_round_trip() -> None:
    poll = from_runtime_state(
        {
            **_v1_runtime_state(),
            "requires_turn_id": True,
            "turn_id_probe_cache_key": "codex:/usr/bin/codex:mtime",
        }
    )

    serialized = to_runtime_state(poll)
    round_tripped = to_runtime_state(from_runtime_state(serialized))

    assert set(serialized) == {field.name for field in fields(CodexPollState)}
    assert round_tripped == serialized
    assert "bound_task_id" not in serialized
    assert "current_task_id" not in serialized
    assert "requires_task_id" not in serialized
    assert "task_id_probe_cache_key" not in serialized


def test_codex_poll_state_missing_requires_turn_id_defaults_false() -> None:
    poll = from_runtime_state({"schema_version": CODEX_POLL_STATE_SCHEMA_VERSION, "request_anchor": "job_serialize"})

    assert poll.requires_turn_id is False
    assert poll.turn_id_probe_cache_key is None


def test_codex_poll_state_unknown_fields_are_tolerated_and_dropped_on_export() -> None:
    poll = from_runtime_state(
        {
            **_v1_runtime_state(),
            "schema_version": CODEX_POLL_STATE_SCHEMA_VERSION,
            "future_unmodeled_field": "ignored",
        }
    )

    assert "future_unmodeled_field" not in to_runtime_state(poll)


def test_codex_poll_state_forward_version_with_required_fields_imports() -> None:
    poll = from_runtime_state(
        {
            **_v1_runtime_state(),
            "schema_version": CODEX_POLL_STATE_SCHEMA_VERSION + 9,
            "requires_turn_id": True,
            "turn_id_probe_cache_key": "codex:/usr/bin/codex:version:mtime",
        }
    )

    assert poll.schema_version == CODEX_POLL_STATE_SCHEMA_VERSION + 9
    assert poll.requires_turn_id is True
    assert poll.bound_turn_id == "turn-1"


def test_codex_poll_state_forward_version_fails_closed_without_identity_fields() -> None:
    try:
        from_runtime_state(
            {
                "schema_version": CODEX_POLL_STATE_SCHEMA_VERSION + 9,
                "request_anchor": "job_serialize",
                "requires_turn_id": True,
            }
        )
    except ValueError as exc:
        assert "requires turn identity" in str(exc)
    else:
        raise AssertionError("forward schema with requires_turn_id must fail without turn_id")


def test_codex_poll_state_v2_to_v3_migration_drops_dead_task_id_fields() -> None:
    poll = from_runtime_state(_fixture("captured-v8.3-runtime-state.json"))
    serialized = to_runtime_state(poll)

    assert poll.schema_version == 3
    assert poll.current_turn_id == "turn-from-v2-task-field"
    assert poll.requires_turn_id is True
    assert poll.turn_id_probe_cache_key == "codex:/usr/bin/codex:v2:123"
    assert "bound_task_id" not in serialized
    assert "current_task_id" not in serialized
    assert "requires_task_id" not in serialized
    assert "task_id_probe_cache_key" not in serialized


def test_codex_poll_state_v2_to_v3_rejects_nonempty_bound_task_id_without_current_task_id() -> None:
    with pytest.raises(Exception) as exc_info:
        from_runtime_state(_fixture("legacy-bound-task-id-without-current-task-id.json"))
    assert exc_info.type is serialization_module.CodexPollStateMigrationError
    assert "legacy-bound-task" in str(exc_info.value)


def test_codex_poll_state_chained_migration_v1_to_v4_fixture() -> None:
    migration_trace: list[int] = []

    def migrate_v3_to_v4(payload: dict[str, object]) -> dict[str, object]:
        migration_trace.append(int(payload["schema_version"]))
        return {**payload, "schema_version": 4, "future_only_field": "dropped"}

    with temporary_poll_state_migration(3, 4, migrate_v3_to_v4):
        poll = from_runtime_state(_v1_runtime_state(), target_schema_version=4)

    assert migration_trace == [3]
    assert poll.schema_version == 4
    assert to_runtime_state(poll)["schema_version"] == 4
    assert "future_only_field" not in to_runtime_state(poll)


def test_build_poll_state_delegates_to_schema_migration() -> None:
    poll = build_poll_state(_submission(_fixture("captured-v8.3-runtime-state.json")))

    assert poll.schema_version == CODEX_POLL_STATE_SCHEMA_VERSION
    assert poll.next_seq == 7
    assert poll.current_turn_id == "turn-from-v2-task-field"


def test_finalization_persists_state_through_serializer() -> None:
    poll = CodexPollState(
        request_anchor="job_serialize",
        next_seq=2,
        anchor_seen=True,
        bound_turn_id="turn-1",
        requires_turn_id=True,
        turn_id_probe_cache_key="codex:/usr/bin/codex:v1:123",
    )

    result = finalize_poll_result(_submission(), poll, state={"cursor": 3})

    assert result is not None
    runtime_state = result.submission.runtime_state
    assert runtime_state["schema_version"] == CODEX_POLL_STATE_SCHEMA_VERSION
    assert runtime_state["requires_turn_id"] is True
    assert runtime_state["turn_id_probe_cache_key"] == "codex:/usr/bin/codex:v1:123"
