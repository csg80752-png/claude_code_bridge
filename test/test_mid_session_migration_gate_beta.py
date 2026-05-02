from __future__ import annotations

from pathlib import Path

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
