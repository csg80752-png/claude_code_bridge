from __future__ import annotations

from types import SimpleNamespace

from ccbd.services.dispatcher_runtime.reply_delivery_runtime import preparation_service
from mailbox_kernel import InboundEventStatus, InboundEventType


class _Backend:
    def __init__(self) -> None:
        self.injected: list[str] = []

    def is_alive(self, pane_id: str) -> bool:
        return True

    def get_pane_content(self, pane_id: str, lines: int = 120) -> str:
        return "❯ "

    def send_text_to_pane(self, pane_id: str, text: str) -> None:
        self.injected.append(text)


def test_second_reply_waits_behind_first_unacked_cmd_head(monkeypatch, tmp_path) -> None:
    backend = _Backend()
    monkeypatch.setenv("CCB_CMD_DELIVERY_MODE", "header_only")
    monkeypatch.setattr(preparation_service, "_discover_cmd_pane_id", lambda dispatcher: "%1")
    monkeypatch.setattr(preparation_service, "_get_tmux_backend", lambda dispatcher: backend)
    monkeypatch.setattr(preparation_service, "_cmd_pane_foreground_command", lambda backend, pane_id: "claude")
    head = SimpleNamespace(
        inbound_event_id="evt_1",
        event_type=InboundEventType.TASK_REPLY,
        status=InboundEventStatus.QUEUED,
        payload_ref="reply:rep_1",
    )
    replies = {
        "rep_1": SimpleNamespace(
            attempt_id="att_1",
            agent_name="agent1",
            reply_id="rep_1",
            terminal_status=SimpleNamespace(value="succeeded"),
            diagnostics={},
            reply="first",
        ),
        "rep_2": SimpleNamespace(
            attempt_id="att_2",
            agent_name="agent1",
            reply_id="rep_2",
            terminal_status=SimpleNamespace(value="succeeded"),
            diagnostics={},
            reply="second",
        ),
    }
    dispatcher = SimpleNamespace(
        _message_bureau_control=SimpleNamespace(
            _mailbox_kernel=SimpleNamespace(head_pending_event=lambda agent_name: head),
            _reply_store=SimpleNamespace(get_latest=lambda reply_id: replies[reply_id]),
            _attempt_store=SimpleNamespace(get_latest=lambda attempt_id: SimpleNamespace(job_id="job_1234abcd")),
        ),
        _layout=SimpleNamespace(project_root=tmp_path),
        _clock=lambda: "2026-05-03T00:00:00Z",
        _cmd_header_only_compatible=True,
        get_job=lambda job_id: SimpleNamespace(job_id="job_1234abcd"),
    )

    preparation_service._deliver_cmd_replies(dispatcher)
    preparation_service._deliver_cmd_replies(dispatcher)

    assert backend.injected == ["[CCB] job=job_1234abcd from=agent1 bytes=5 pend=ccb-pend"]
    assert "rep_2" not in (tmp_path / ".ccb" / "ccbd" / "cmd-delivered-cache.jsonl").read_text(encoding="utf-8")
