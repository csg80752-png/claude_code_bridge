from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from ccbd.services.dispatcher_runtime.reply_delivery_runtime import preparation_service
from mailbox_kernel import InboundEventStatus, InboundEventType
from ccbd.services.dispatcher_runtime.reply_delivery_runtime.cmd_transport_planner import (
    CmdDeliveryMode,
    beta_header_only_allowed,
    resolve_cmd_delivery_mode,
)


def test_beta_gate_keeps_stale_session_on_full_body(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("CCB_CMD_DELIVERY_MODE", raising=False)
    monkeypatch.delenv("CCB_HEADER_ONLY", raising=False)

    assert resolve_cmd_delivery_mode(project_root=tmp_path).mode is CmdDeliveryMode.FULL_BODY
    assert beta_header_only_allowed(smoke_passed=False) is False


def test_beta_gate_allows_header_only_after_restart_smoke(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CCB_CMD_DELIVERY_MODE", "header_only")

    assert beta_header_only_allowed(smoke_passed=True) is True
    assert resolve_cmd_delivery_mode(project_root=tmp_path).mode is CmdDeliveryMode.HEADER_ONLY


def test_beta_deploy_checklist_documents_restart_and_fallback() -> None:
    checklist = (
        Path(__file__).resolve().parents[1] / "docs" / "v8.3.2-header-only-deploy-checklist.md"
    ).read_text(encoding="utf-8")

    assert "Restart the cmd-pane Claude session" in checklist
    assert "auto-pend smoke test" in checklist
    assert "CCB_CMD_DELIVERY_MODE=full_body" in checklist
    assert "CCB_CMD_DELIVERY_MODE=header_only" in checklist


class _Backend:
    def __init__(self) -> None:
        self.injected: list[tuple[str, str]] = []

    def is_alive(self, pane_id: str) -> bool:
        return True

    def get_pane_content(self, pane_id: str, lines: int = 120) -> str:
        return "❯ "

    def send_text_to_pane(self, pane_id: str, text: str) -> None:
        self.injected.append((pane_id, text))


def _dispatcher(tmp_path: Path, *, header_only_compatible: bool):
    head = SimpleNamespace(
        inbound_event_id="evt_1",
        event_type=InboundEventType.TASK_REPLY,
        status=InboundEventStatus.QUEUED,
        payload_ref="reply:rep_1",
    )
    reply = SimpleNamespace(
        attempt_id="att_1",
        agent_name="agent1",
        reply_id="rep_1",
        terminal_status=SimpleNamespace(value="succeeded"),
        diagnostics={},
        reply="body for cmd",
    )
    kernel = SimpleNamespace(head_pending_event=lambda agent_name: head)
    reply_store = SimpleNamespace(get_latest=lambda reply_id: reply)
    attempt_store = SimpleNamespace(get_latest=lambda attempt_id: SimpleNamespace(job_id="job_1234abcd"))
    dispatcher = SimpleNamespace(
        _message_bureau_control=SimpleNamespace(
            _mailbox_kernel=kernel,
            _reply_store=reply_store,
            _attempt_store=attempt_store,
        ),
        _layout=SimpleNamespace(project_root=tmp_path),
        _clock=lambda: "2026-05-03T00:00:00Z",
        _cmd_header_only_compatible=header_only_compatible,
        get_job=lambda job_id: SimpleNamespace(job_id="job_1234abcd", request=SimpleNamespace(task_id="task_1")),
    )
    return dispatcher


def test_beta_gate_stale_session_uses_full_body_and_does_not_cache_header(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CCB_CMD_DELIVERY_MODE", "header_only")
    backend = _Backend()
    monkeypatch.setattr(preparation_service, "_discover_cmd_pane_id", lambda dispatcher: "%1")
    monkeypatch.setattr(preparation_service, "_get_tmux_backend", lambda dispatcher: backend)
    monkeypatch.setattr(preparation_service, "_cmd_pane_foreground_command", lambda backend, pane_id: "claude")

    dispatcher = _dispatcher(tmp_path, header_only_compatible=False)
    preparation_service._deliver_cmd_replies(dispatcher)

    assert len(backend.injected) == 1
    assert "body for cmd" in backend.injected[0][1]
    assert not backend.injected[0][1].startswith("[CCB] ")
    metrics = (tmp_path / ".ccb" / "metrics" / "body_read_followup.jsonl").read_text(encoding="utf-8")
    assert "cmd_delivery_success" in metrics
    assert "cmd_delivery_header_inject_success" not in metrics


def test_beta_gate_restarted_smoke_passed_session_injects_header_and_caches(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CCB_CMD_DELIVERY_MODE", "header_only")
    backend = _Backend()
    monkeypatch.setattr(preparation_service, "_discover_cmd_pane_id", lambda dispatcher: "%1")
    monkeypatch.setattr(preparation_service, "_get_tmux_backend", lambda dispatcher: backend)
    monkeypatch.setattr(preparation_service, "_cmd_pane_foreground_command", lambda backend, pane_id: "claude")

    dispatcher = _dispatcher(tmp_path, header_only_compatible=True)
    preparation_service._deliver_cmd_replies(dispatcher)

    assert backend.injected == [("%1", "[CCB] job=job_1234abcd from=agent1 bytes=12 pend=ccb-pend")]
    cache_path = tmp_path / ".ccb" / "ccbd" / "cmd-delivered-cache.jsonl"
    assert cache_path.exists()
    assert "rep_1" in cache_path.read_text(encoding="utf-8")
