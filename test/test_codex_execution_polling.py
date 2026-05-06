from __future__ import annotations

from types import SimpleNamespace

from completion.models import CompletionItemKind, CompletionSourceKind
from provider_backends.codex.comm_runtime.log_entries import extract_entry
from provider_backends.codex.execution_runtime.polling import poll_submission
from provider_backends.codex.execution_runtime.state_machine_runtime import (
    CodexPollState,
    handle_assistant_entry,
    handle_terminal_entry,
)
from provider_execution.base import ProviderSubmission


def _submission() -> ProviderSubmission:
    return ProviderSubmission(
        job_id="job_1",
        agent_name="agent1",
        provider="codex",
        accepted_at="2026-04-06T00:00:00Z",
        ready_at="2026-04-06T00:00:00Z",
        source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
        reply="",
        runtime_state={"state": {}, "anchor_seen": True, "bound_turn_id": "turn-1"},
    )


class _RawEntryReader:
    def __init__(self, raw_entries: list[dict[str, object]]) -> None:
        self._raw_entries = raw_entries

    def try_get_entries(self, state: dict[str, object]):
        if state.get("done"):
            return [], state
        entries = []
        for raw_entry in self._raw_entries:
            normalized = extract_entry(raw_entry)
            if normalized is not None:
                entries.append(normalized)
        return entries, {"done": True}


def test_poll_submission_processes_entries_until_terminal(monkeypatch) -> None:
    submission = _submission()
    poll = SimpleNamespace(anchor_seen=True, reached_terminal=False)
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(
        "provider_backends.codex.execution_runtime.polling_runtime.prepare_active_poll",
        lambda submission, now: SimpleNamespace(reader=object()),
    )
    monkeypatch.setattr(
        "provider_backends.codex.execution_runtime.polling_runtime.build_poll_state",
        lambda submission: poll,
    )
    monkeypatch.setattr(
        "provider_backends.codex.execution_runtime.polling_runtime.read_entries",
        lambda reader, state: (
            [
                {"role": "user", "text": "hello"},
                {"role": "assistant", "text": "answer"},
                {"role": "system", "payload_type": "task_complete"},
            ],
            {"cursor": 1},
        ),
    )
    monkeypatch.setattr(
        "provider_backends.codex.execution_runtime.polling_runtime.apply_session_rotation",
        lambda submission, poll, new_session_path, now: calls.append(("rotate", new_session_path)),
    )
    monkeypatch.setattr(
        "provider_backends.codex.execution_runtime.polling_runtime.state_session_path",
        lambda state: "session-1",
    )
    monkeypatch.setattr(
        "provider_backends.codex.execution_runtime.polling_runtime.update_binding_refs",
        lambda poll, entry: calls.append(("bind", entry["role"])),
    )
    monkeypatch.setattr(
        "provider_backends.codex.execution_runtime.polling_runtime.handle_user_entry",
        lambda submission, poll, text, now: calls.append(("user", text)),
    )
    monkeypatch.setattr(
        "provider_backends.codex.execution_runtime.polling_runtime.handle_assistant_entry",
        lambda submission, poll, entry, now: calls.append(("assistant", entry["text"])),
    )
    monkeypatch.setattr(
        "provider_backends.codex.execution_runtime.polling_runtime.handle_terminal_entry",
        lambda submission, poll, entry, now: calls.append(("terminal", entry["payload_type"]))
        or setattr(poll, "reached_terminal", True),
    )
    monkeypatch.setattr(
        "provider_backends.codex.execution_runtime.polling_runtime.finalize_poll_result",
        lambda submission, poll, state: {"state": state, "calls": list(calls)},
    )

    result = poll_submission(submission, now="2026-04-06T00:01:00Z")

    assert result["state"] == {"cursor": 1}
    assert result["calls"] == [
        ("rotate", "session-1"),
        ("bind", "user"),
        ("user", "hello"),
        ("bind", "assistant"),
        ("assistant", "answer"),
        ("bind", "system"),
        ("terminal", "task_complete"),
    ]


def test_poll_submission_preserves_top_level_turn_id_from_raw_codex_entries(monkeypatch) -> None:
    submission = _submission()
    submission.runtime_state.update(
        {
            "anchor_seen": False,
            "bound_turn_id": "",
            "requires_turn_id": True,
        }
    )
    raw_entries = [
        {"type": "event_msg", "turn_id": "turn-top", "payload": {"type": "task_started"}},
        {
            "type": "response_item",
            "turn_id": "turn-top",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "CCB_REQ_ID: job_1\n\nhello"}],
            },
        },
        {
            "type": "event_msg",
            "turn_id": "turn-top",
            "payload": {"type": "assistant_message", "role": "assistant", "message": "answer"},
        },
        {
            "type": "event_msg",
            "turn_id": "turn-top",
            "payload": {"type": "task_complete", "last_agent_message": "answer"},
        },
    ]

    monkeypatch.setattr(
        "provider_backends.codex.execution_runtime.polling_runtime.prepare_active_poll",
        lambda submission, now: SimpleNamespace(reader=_RawEntryReader(raw_entries)),
    )
    monkeypatch.setattr(
        "provider_backends.codex.execution_runtime.polling_runtime.apply_session_rotation",
        lambda submission, poll, new_session_path, now: None,
    )
    monkeypatch.setattr(
        "provider_backends.codex.execution_runtime.polling_runtime.state_session_path",
        lambda state: "",
    )

    result = poll_submission(submission, now="2026-04-06T00:01:00Z")

    assert result is not None
    assert result.submission.reply == "answer"
    assert result.submission.runtime_state["bound_turn_id"] == "turn-top"
    assert result.submission.runtime_state["bound_turn_contaminated"] is False


def test_poll_submission_binds_current_codex_turn_context_for_unkeyed_messages(monkeypatch) -> None:
    submission = _submission()
    submission.runtime_state.update(
        {
            "anchor_seen": False,
            "bound_turn_id": "",
            "requires_turn_id": True,
        }
    )
    raw_entries = [
        {
            "type": "event_msg",
            "payload": {"type": "task_started", "turn_id": "turn-live"},
        },
        {
            "type": "turn_context",
            "payload": {"turn_id": "turn-live"},
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "CCB_REQ_ID: job_1\n\nhello"}],
            },
        },
        {
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "CCB_REQ_ID: job_1\n\nhello"},
        },
        {
            "type": "event_msg",
            "payload": {"type": "agent_message", "message": "answer", "phase": "final_answer"},
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "answer"}],
                "phase": "final_answer",
            },
        },
        {
            "type": "event_msg",
            "payload": {"type": "task_complete", "turn_id": "turn-live", "last_agent_message": "answer"},
        },
    ]

    monkeypatch.setattr(
        "provider_backends.codex.execution_runtime.polling_runtime.prepare_active_poll",
        lambda submission, now: SimpleNamespace(reader=_RawEntryReader(raw_entries)),
    )
    monkeypatch.setattr(
        "provider_backends.codex.execution_runtime.polling_runtime.apply_session_rotation",
        lambda submission, poll, new_session_path, now: None,
    )
    monkeypatch.setattr(
        "provider_backends.codex.execution_runtime.polling_runtime.state_session_path",
        lambda state: "",
    )

    result = poll_submission(submission, now="2026-04-06T00:01:00Z")

    assert result is not None
    assert result.submission.reply == "answer"
    assert result.submission.runtime_state["bound_turn_id"] == "turn-live"
    assert result.submission.runtime_state["bound_turn_contaminated"] is False


def test_poll_submission_ignores_duplicate_anchor_payload_with_embedded_req_id(monkeypatch) -> None:
    submission = _submission()
    submission.runtime_state.update(
        {
            "anchor_seen": False,
            "bound_turn_id": "",
            "requires_turn_id": True,
        }
    )
    user_text = "CCB_REQ_ID: job_1\n\nCCB_REQ_ID: statusline_fresh_agent2_after_model_restart\n\nhello"
    raw_entries = [
        {
            "type": "event_msg",
            "payload": {"type": "task_started", "turn_id": "turn-live"},
        },
        {
            "type": "turn_context",
            "payload": {"turn_id": "turn-live"},
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": user_text}],
            },
        },
        {
            "type": "event_msg",
            "payload": {"type": "user_message", "message": user_text},
        },
        {
            "type": "event_msg",
            "payload": {"type": "agent_message", "message": "answer", "phase": "final_answer"},
        },
        {
            "type": "event_msg",
            "payload": {"type": "task_complete", "turn_id": "turn-live", "last_agent_message": "answer"},
        },
    ]

    monkeypatch.setattr(
        "provider_backends.codex.execution_runtime.polling_runtime.prepare_active_poll",
        lambda submission, now: SimpleNamespace(reader=_RawEntryReader(raw_entries)),
    )
    monkeypatch.setattr(
        "provider_backends.codex.execution_runtime.polling_runtime.apply_session_rotation",
        lambda submission, poll, new_session_path, now: None,
    )
    monkeypatch.setattr(
        "provider_backends.codex.execution_runtime.polling_runtime.state_session_path",
        lambda state: "",
    )

    result = poll_submission(submission, now="2026-04-06T00:01:00Z")

    assert result is not None
    assert result.submission.reply == "answer"
    assert result.submission.runtime_state["bound_turn_id"] == "turn-live"
    assert result.submission.runtime_state["bound_turn_contaminated"] is False


def test_handle_assistant_entry_records_final_answer() -> None:
    poll = CodexPollState(
        request_anchor="job_1",
        next_seq=1,
        anchor_seen=True,
        bound_turn_id="turn-1",
        reply_buffer="",
        last_agent_message="",
        last_final_answer="",
        last_assistant_message="",
        last_assistant_signature="",
        session_path="/tmp/session.jsonl",
    )

    handle_assistant_entry(
        _submission(),
        poll,
        {"text": "final answer", "phase": "final_answer", "id": "evt-1", "turn_id": "turn-1"},
        now="2026-04-06T00:01:00Z",
    )

    assert poll.last_final_answer == "final answer"
    assert poll.items[0].kind is CompletionItemKind.ASSISTANT_CHUNK
    assert poll.items[0].payload["phase"] == "final_answer"
    assert poll.items[0].payload["turn_id"] == "turn-1"
    assert "task_id" not in poll.items[0].payload


def test_handle_terminal_entry_emits_turn_aborted_payload() -> None:
    poll = CodexPollState(
        request_anchor="job_1",
        next_seq=2,
        anchor_seen=True,
        bound_turn_id="turn-1",
        reply_buffer="partial",
        last_agent_message="",
        last_final_answer="",
        last_assistant_message="partial",
        last_assistant_signature="",
        session_path="/tmp/session.jsonl",
    )

    handle_terminal_entry(
        _submission(),
        poll,
        {
            "payload_type": "turn_aborted",
            "reason": "cancelled",
            "text": "user cancelled",
            "turn_id": "turn-1",
        },
        now="2026-04-06T00:01:00Z",
    )

    assert poll.reached_terminal is True
    assert poll.items[0].kind is CompletionItemKind.TURN_ABORTED
    assert poll.items[0].payload["reason"] == "cancelled"
    assert poll.items[0].payload["status"] == "cancelled"
    assert poll.items[0].payload["error_message"] == "user cancelled"
