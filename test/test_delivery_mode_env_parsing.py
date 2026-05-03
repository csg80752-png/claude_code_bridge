from __future__ import annotations

import json

import pytest

from ccbd.services.dispatcher_runtime.reply_delivery_runtime.cmd_transport_planner import (
    CmdDeliveryMode,
    effective_cmd_delivery_mode,
    resolve_cmd_delivery_mode,
)


def _events(project_root):
    path = project_root / ".ccb" / "metrics" / "body_read_followup.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_delivery_mode_defaults_to_full_body_for_beta_gate(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("CCB_CMD_DELIVERY_MODE", raising=False)
    monkeypatch.delenv("CCB_HEADER_ONLY", raising=False)

    result = resolve_cmd_delivery_mode(project_root=tmp_path)

    assert result.mode is CmdDeliveryMode.FULL_BODY
    assert result.reason == "default_beta_full_body"


def test_delivery_mode_accepts_explicit_header_only(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CCB_CMD_DELIVERY_MODE", " header_only ")
    monkeypatch.setenv("CCB_HEADER_ONLY", "0")

    result = resolve_cmd_delivery_mode(project_root=tmp_path)

    assert result.mode is CmdDeliveryMode.HEADER_ONLY
    assert result.reason == "explicit"
    assert any(event["event"] == "cmd_delivery_legacy_env_seen" for event in _events(tmp_path))


@pytest.mark.parametrize("value", ["", "header", "true", "headers"])
def test_delivery_mode_malformed_new_env_fails_closed(monkeypatch, tmp_path, value: str) -> None:
    monkeypatch.setenv("CCB_CMD_DELIVERY_MODE", value)
    monkeypatch.setenv("CCB_HEADER_ONLY", "1")

    result = resolve_cmd_delivery_mode(project_root=tmp_path)

    assert result.mode is CmdDeliveryMode.FULL_BODY
    assert result.reason == "invalid"
    events = _events(tmp_path)
    assert events[-1]["event"] == "cmd_delivery_mode_invalid"
    assert events[-1]["raw_value"] == value


@pytest.mark.parametrize(
    ("legacy", "expected"),
    [
        ("1", CmdDeliveryMode.HEADER_ONLY),
        ("true", CmdDeliveryMode.HEADER_ONLY),
        ("yes", CmdDeliveryMode.HEADER_ONLY),
        ("on", CmdDeliveryMode.HEADER_ONLY),
        ("0", CmdDeliveryMode.FULL_BODY),
        ("false", CmdDeliveryMode.FULL_BODY),
        ("no", CmdDeliveryMode.FULL_BODY),
        ("off", CmdDeliveryMode.FULL_BODY),
    ],
)
def test_legacy_header_only_mapping(monkeypatch, tmp_path, legacy: str, expected: CmdDeliveryMode) -> None:
    monkeypatch.delenv("CCB_CMD_DELIVERY_MODE", raising=False)
    monkeypatch.setenv("CCB_HEADER_ONLY", legacy)

    result = resolve_cmd_delivery_mode(project_root=tmp_path)

    assert result.mode is expected
    assert result.reason == "legacy"


def test_legacy_header_only_malformed_fails_closed(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("CCB_CMD_DELIVERY_MODE", raising=False)
    monkeypatch.setenv("CCB_HEADER_ONLY", "sometimes")

    result = resolve_cmd_delivery_mode(project_root=tmp_path)

    assert result.mode is CmdDeliveryMode.FULL_BODY
    assert result.reason == "invalid_legacy"
    assert _events(tmp_path)[-1]["event"] == "cmd_delivery_mode_invalid"


def test_delivery_mode_result_is_restart_only_after_startup_resolution(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CCB_CMD_DELIVERY_MODE", "header_only")
    startup_result = resolve_cmd_delivery_mode(project_root=tmp_path, header_only_compatible=True)

    monkeypatch.setenv("CCB_CMD_DELIVERY_MODE", "full_body")
    monkeypatch.setenv("CCB_CMD_HEADER_ONLY_COMPATIBLE", "0")

    assert startup_result.mode is CmdDeliveryMode.HEADER_ONLY
    assert effective_cmd_delivery_mode(startup_result) is CmdDeliveryMode.HEADER_ONLY


def test_hidden_compat_env_is_ignored_without_marker(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CCB_CMD_DELIVERY_MODE", "header_only")
    monkeypatch.setenv("CCB_CMD_HEADER_ONLY_COMPATIBLE", "1")

    result = resolve_cmd_delivery_mode(project_root=tmp_path)

    assert result.mode is CmdDeliveryMode.HEADER_ONLY
    assert result.header_only_compatible is False
    assert effective_cmd_delivery_mode(result) is CmdDeliveryMode.FULL_BODY
