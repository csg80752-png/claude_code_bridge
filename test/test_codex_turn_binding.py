from __future__ import annotations

from types import SimpleNamespace

from completion.models import CompletionItemKind, CompletionSourceKind
from provider_backends.codex.execution_runtime.polling import poll_submission
from provider_execution.base import ProviderSubmission


def _submission(*, runtime_state: dict[str, object] | None = None) -> ProviderSubmission:
    return ProviderSubmission(
        job_id="job_1",
        agent_name="agent2",
        provider="codex",
        accepted_at="2026-04-24T00:00:00Z",
        ready_at="2026-04-24T00:00:00Z",
        source_kind=CompletionSourceKind.PROTOCOL_EVENT_STREAM,
        reply="",
        runtime_state={
            "state": {},
            "request_anchor": "job_1",
            "anchor_seen": False,
            **(runtime_state or {}),
        },
    )


def _poll_entries(
    monkeypatch,
    entries: list[dict[str, object]],
    *,
    runtime_state: dict[str, object] | None = None,
):
    queue = [dict(entry) for entry in entries]

    def fake_read_entries(reader, state):
        del reader
        index = int(state.get("index", 0))
        if index >= len(queue):
            return [], state
        return [queue[index]], {"index": index + 1}

    monkeypatch.setattr(
        "provider_backends.codex.execution_runtime.polling_runtime.prepare_active_poll",
        lambda submission, now: SimpleNamespace(reader=object()),
    )
    monkeypatch.setattr(
        "provider_backends.codex.execution_runtime.polling_runtime.read_entries",
        fake_read_entries,
    )
    monkeypatch.setattr(
        "provider_backends.codex.execution_runtime.polling_runtime.apply_session_rotation",
        lambda submission, poll, new_session_path, now: None,
    )
    monkeypatch.setattr(
        "provider_backends.codex.execution_runtime.polling_runtime.state_session_path",
        lambda state: "",
    )
    return poll_submission(_submission(runtime_state=runtime_state), now="2026-04-24T00:00:01Z")


def test_parser_filters_foreign_turn_entries(monkeypatch) -> None:
    result = _poll_entries(
        monkeypatch,
        [
            {"role": "meta", "entry_type": "event_msg", "payload_type": "task_started", "turn_id": "turn-ours"},
            {"role": "meta", "entry_type": "turn_context", "payload_type": "turn_context", "turn_id": "turn-ours"},
            {"role": "user", "text": "CCB_REQ_ID: job_1\n\nprompt"},
            {"role": "assistant", "text": "ours partial", "task_id": "task-ours", "timestamp": "2026-04-24T00:00:02Z"},
            {"role": "meta", "entry_type": "event_msg", "payload_type": "task_started", "turn_id": "turn-foreign"},
            {"role": "meta", "entry_type": "turn_context", "payload_type": "turn_context", "turn_id": "turn-foreign"},
            {"role": "assistant", "text": "foreign partial", "timestamp": "2026-04-24T00:00:03Z"},
        ],
    )

    assert result is not None
    assert [item.kind for item in result.items] == [
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.ASSISTANT_CHUNK,
    ]
    assert result.items[-1].payload["text"] == "ours partial"
    assert result.decision is None


def test_parser_rejects_foreign_task_complete(monkeypatch) -> None:
    result = _poll_entries(
        monkeypatch,
        [
            {"role": "meta", "entry_type": "turn_context", "payload_type": "turn_context", "turn_id": "turn-ours"},
            {"role": "user", "text": "CCB_REQ_ID: job_1\n\nprompt"},
            {
                "role": "system",
                "entry_type": "event_msg",
                "payload_type": "task_complete",
                "turn_id": "turn-foreign",
                "last_agent_message": "foreign final",
                "timestamp": "2026-04-24T00:00:03Z",
            },
        ],
    )

    assert result is not None
    assert [item.kind for item in result.items] == [CompletionItemKind.ANCHOR_SEEN]
    assert result.decision is None
    assert result.submission.reply == ""


def test_parser_accepts_bound_turn_complete(monkeypatch) -> None:
    result = _poll_entries(
        monkeypatch,
        [
            {
                "role": "meta",
                "entry_type": "event_msg",
                "payload_type": "task_started",
                "turn_id": "turn-ours",
                "task_id": "task-ours",
            },
            {"role": "meta", "entry_type": "turn_context", "payload_type": "turn_context", "turn_id": "turn-ours"},
            {"role": "user", "text": "CCB_REQ_ID: job_1\n\nprompt"},
            {"role": "assistant", "text": "ours partial", "task_id": "task-ours", "timestamp": "2026-04-24T00:00:02Z"},
            {
                "role": "system",
                "entry_type": "event_msg",
                "payload_type": "task_complete",
                "turn_id": "turn-ours",
                "task_id": "task-ours",
                "last_agent_message": "ours final",
                "timestamp": "2026-04-24T00:00:03Z",
            },
        ],
    )

    assert result is not None
    assert [item.kind for item in result.items] == [
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.ASSISTANT_CHUNK,
        CompletionItemKind.TURN_BOUNDARY,
    ]
    assert result.items[-1].payload["turn_id"] == "turn-ours"
    assert result.items[-1].payload["task_id"] == "task-ours"
    assert result.items[-1].payload["last_agent_message"] == "ours final"


def test_concurrent_multi_agent_session_sharing_no_cross_completion(monkeypatch) -> None:
    result = _poll_entries(
        monkeypatch,
        [
            {"role": "meta", "entry_type": "turn_context", "payload_type": "turn_context", "turn_id": "turn-ours"},
            {"role": "user", "text": "CCB_REQ_ID: job_1\n\nprompt"},
            {
                "role": "assistant",
                "text": "foreign answer",
                "phase": "final_answer",
                "timestamp": "2026-04-24T00:00:02Z",
            },
            {
                "role": "system",
                "entry_type": "event_msg",
                "payload_type": "task_complete",
                "turn_id": "turn-foreign",
                "last_agent_message": "foreign answer",
                "timestamp": "2026-04-24T00:00:03Z",
            },
        ],
    )

    assert result is not None
    assert [item.kind for item in result.items] == [CompletionItemKind.ANCHOR_SEEN]
    assert result.decision is None
    assert result.submission.runtime_state["bound_turn_id"] == "turn-ours"
    assert result.submission.reply == ""


def test_same_turn_foreign_anchor_contaminates_unkeyed_completion(monkeypatch) -> None:
    result = _poll_entries(
        monkeypatch,
        [
            {
                "role": "meta",
                "entry_type": "event_msg",
                "payload_type": "task_started",
                "turn_id": "turn-shared",
                "task_id": "task-ours",
            },
            {"role": "meta", "entry_type": "turn_context", "payload_type": "turn_context", "turn_id": "turn-shared"},
            {"role": "user", "text": "CCB_REQ_ID: job_1\n\nprompt"},
            {"role": "user", "text": "CCB_REQ_ID: job_foreign\n\nother prompt", "turn_id": "turn-shared"},
            {"role": "assistant", "text": "foreign same-turn answer", "turn_id": "turn-shared"},
            {
                "role": "system",
                "entry_type": "event_msg",
                "payload_type": "task_complete",
                "turn_id": "turn-shared",
                "last_agent_message": "foreign final",
                "timestamp": "2026-04-24T00:00:03Z",
            },
        ],
    )

    assert result is not None
    assert [item.kind for item in result.items] == [CompletionItemKind.ANCHOR_SEEN]
    assert result.decision is None
    assert result.submission.runtime_state["bound_turn_contaminated"] is True
    assert result.submission.reply == ""


def test_same_turn_foreign_anchor_rejects_later_duplicate_task_id_completion(monkeypatch) -> None:
    result = _poll_entries(
        monkeypatch,
        [
            {
                "role": "meta",
                "entry_type": "event_msg",
                "payload_type": "task_started",
                "turn_id": "turn-shared",
                "task_id": "task-ours",
            },
            {"role": "meta", "entry_type": "turn_context", "payload_type": "turn_context", "turn_id": "turn-shared"},
            {"role": "user", "text": "CCB_REQ_ID: job_1\n\nprompt"},
            {"role": "user", "text": "CCB_REQ_ID: job_foreign\n\nother prompt", "turn_id": "turn-shared"},
            {"role": "assistant", "text": "duplicate task after contamination", "turn_id": "turn-shared", "task_id": "task-ours"},
            {
                "role": "system",
                "entry_type": "event_msg",
                "payload_type": "task_complete",
                "turn_id": "turn-shared",
                "task_id": "task-ours",
                "last_agent_message": "duplicate task final",
                "timestamp": "2026-04-24T00:00:03Z",
            },
        ],
    )

    assert result is not None
    assert [item.kind for item in result.items] == [CompletionItemKind.ANCHOR_SEEN]
    assert result.decision is None
    assert result.submission.runtime_state["bound_turn_contaminated"] is True
    assert result.submission.reply == ""


def test_taskless_completion_before_foreign_anchor_is_accepted_for_current_codex_logs(monkeypatch) -> None:
    result = _poll_entries(
        monkeypatch,
        [
            {
                "role": "meta",
                "entry_type": "event_msg",
                "payload_type": "task_started",
                "turn_id": "turn-shared",
                "task_id": "task-ours",
            },
            {"role": "meta", "entry_type": "turn_context", "payload_type": "turn_context", "turn_id": "turn-shared"},
            {"role": "user", "text": "CCB_REQ_ID: job_1\n\nprompt"},
            {"role": "assistant", "text": "unkeyed same-turn answer", "turn_id": "turn-shared"},
            {
                "role": "system",
                "entry_type": "event_msg",
                "payload_type": "task_complete",
                "turn_id": "turn-shared",
                "last_agent_message": "unkeyed final",
                "timestamp": "2026-04-24T00:00:03Z",
            },
        ],
    )

    assert result is not None
    assert [item.kind for item in result.items] == [
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.ASSISTANT_CHUNK,
        CompletionItemKind.TURN_BOUNDARY,
    ]
    assert result.items[-1].payload["last_agent_message"] == "unkeyed final"


def test_same_turn_contamination_survives_exported_restart_state(monkeypatch) -> None:
    from provider_execution.codex import CodexProviderAdapter

    contaminated = _poll_entries(
        monkeypatch,
        [
            {
                "role": "meta",
                "entry_type": "event_msg",
                "payload_type": "task_started",
                "turn_id": "turn-shared",
                "task_id": "task-ours",
            },
            {"role": "meta", "entry_type": "turn_context", "payload_type": "turn_context", "turn_id": "turn-shared"},
            {"role": "user", "text": "CCB_REQ_ID: job_1\n\nprompt"},
            {"role": "user", "text": "CCB_REQ_ID: job_foreign\n\nother prompt", "turn_id": "turn-shared"},
        ],
    )
    assert contaminated is not None
    exported = CodexProviderAdapter().export_runtime_state(contaminated.submission)
    assert exported["bound_turn_contaminated"] is True

    restarted = _poll_entries(
        monkeypatch,
        [
            {"role": "assistant", "text": "foreign same-turn answer", "turn_id": "turn-shared"},
            {
                "role": "system",
                "entry_type": "event_msg",
                "payload_type": "task_complete",
                "turn_id": "turn-shared",
                "last_agent_message": "foreign final",
                "timestamp": "2026-04-24T00:00:03Z",
            },
        ],
        runtime_state=exported,
    )

    assert restarted is None or restarted.submission.reply == ""


def test_pre_bind_entries_discarded(monkeypatch) -> None:
    result = _poll_entries(
        monkeypatch,
        [
            {"role": "assistant", "text": "stale", "timestamp": "2026-04-24T00:00:00Z"},
            {
                "role": "system",
                "entry_type": "event_msg",
                "payload_type": "task_complete",
                "turn_id": "turn-foreign",
                "last_agent_message": "stale",
                "timestamp": "2026-04-24T00:00:00Z",
            },
            {"role": "meta", "entry_type": "event_msg", "payload_type": "task_started", "turn_id": "turn-ours"},
            {"role": "meta", "entry_type": "turn_context", "payload_type": "turn_context", "turn_id": "turn-ours"},
            {"role": "user", "text": "CCB_REQ_ID: job_1\n\nprompt"},
            {"role": "assistant", "text": "ours", "timestamp": "2026-04-24T00:00:02Z"},
            {
                "role": "system",
                "entry_type": "event_msg",
                "payload_type": "task_complete",
                "turn_id": "turn-ours",
                "last_agent_message": "ours",
                "timestamp": "2026-04-24T00:00:03Z",
            },
        ],
    )

    assert result is not None
    assert [item.kind for item in result.items] == [
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.ASSISTANT_CHUNK,
        CompletionItemKind.TURN_BOUNDARY,
    ]
    assert result.items[-1].payload["last_agent_message"] == "ours"
