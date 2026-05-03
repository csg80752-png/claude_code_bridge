from __future__ import annotations

import json

from ccbd.services.dispatcher_runtime.reply_delivery_runtime.cmd_header_compatibility import (
    write_cmd_header_only_compatibility_marker,
)
from ccbd.services.dispatcher_runtime.reply_delivery_runtime.cmd_transport_planner import (
    CmdDeliveryMode,
    effective_cmd_delivery_mode,
    resolve_cmd_delivery_mode,
)


def test_hidden_compat_env_does_not_enable_header_only(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CCB_CMD_DELIVERY_MODE", "header_only")
    monkeypatch.setenv("CCB_CMD_HEADER_ONLY_COMPATIBLE", "1")

    result = resolve_cmd_delivery_mode(project_root=tmp_path)

    assert result.mode is CmdDeliveryMode.HEADER_ONLY
    assert result.header_only_compatible is False
    assert effective_cmd_delivery_mode(result) is CmdDeliveryMode.FULL_BODY


def test_valid_deploy_smoke_marker_enables_effective_header_only(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CCB_CMD_DELIVERY_MODE", "header_only")
    write_cmd_header_only_compatibility_marker(
        tmp_path,
        smoke_job_id="job_1234abcd",
        smoke_reply_id="rep_smoke",
        cmd_pane_id="%1",
    )

    result = resolve_cmd_delivery_mode(project_root=tmp_path)

    assert result.mode is CmdDeliveryMode.HEADER_ONLY
    assert result.header_only_compatible is True
    assert effective_cmd_delivery_mode(result) is CmdDeliveryMode.HEADER_ONLY


def test_mismatched_deploy_smoke_marker_fails_closed(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CCB_CMD_DELIVERY_MODE", "header_only")
    marker = write_cmd_header_only_compatibility_marker(
        tmp_path,
        smoke_job_id="job_1234abcd",
        smoke_reply_id="rep_smoke",
        cmd_pane_id="%1",
    )
    payload = json.loads(marker.read_text(encoding="utf-8"))
    payload["project_id"] = "wrong-project"
    marker.write_text(json.dumps(payload), encoding="utf-8")

    result = resolve_cmd_delivery_mode(project_root=tmp_path)

    assert result.mode is CmdDeliveryMode.HEADER_ONLY
    assert result.header_only_compatible is False
    assert effective_cmd_delivery_mode(result) is CmdDeliveryMode.FULL_BODY
