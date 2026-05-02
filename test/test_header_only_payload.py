from __future__ import annotations

from types import SimpleNamespace

import pytest

from ccbd.services.dispatcher_runtime.reply_delivery_runtime import preparation_service
from ccbd.services.dispatcher_runtime.reply_delivery_runtime.cmd_transport_planner import (
    CMD_HEADER_RE,
    MAX_CMD_HEADER_BYTES_FIELD,
    MAX_CMD_HEADER_LEN,
    CmdHeaderValidationError,
    parse_cmd_header_tokens,
    plan_cmd_delivery,
)


def _reply(*, body: str = "hello", agent_name: str = "agent2", attempt_id: str = "att-1"):
    return SimpleNamespace(
        attempt_id=attempt_id,
        agent_name=agent_name,
        reply_id="rep_1",
        terminal_status=SimpleNamespace(value="succeeded"),
        diagnostics={},
        reply=body,
    )


def _dispatcher(*, job_id: str | None = "job_1234abcd"):
    attempt = SimpleNamespace(job_id=job_id or "")
    source_job = None if job_id is None else SimpleNamespace(job_id=job_id, request=SimpleNamespace(task_id="task-1"))
    return SimpleNamespace(
        _message_bureau_control=SimpleNamespace(
            _attempt_store=SimpleNamespace(get_latest=lambda attempt_id: attempt)
        ),
        get_job=lambda jid: source_job,
    )


@pytest.mark.parametrize(
    "bad",
    [
        "agent1`",
        "agent1$",
        "agent1;",
        "agent\n1",
        "agent\033]0;x\007",
        "agent—",
    ],
)
def test_prepare_cmd_payload_rejects_shell_and_control_chars(bad: str) -> None:
    with pytest.raises(CmdHeaderValidationError):
        preparation_service._prepare_cmd_payload(
            sender_id=bad,
            body_bytes=12,
            source_job_id="job_1234abcd",
        )


def test_prepare_cmd_payload_sanitizes_bad_source_job_by_falling_back_to_cmd_target() -> None:
    header = preparation_service._prepare_cmd_payload(
        sender_id="agent1",
        body_bytes=12,
        source_job_id="job_1234abcd;rm",
    )

    assert header == "[CCB] job=target=cmd from=agent1 bytes=12 pend=ccb-pend"


def test_prepare_cmd_payload_uses_target_cmd_for_missing_source_job() -> None:
    header = preparation_service._prepare_cmd_payload(
        sender_id="agent1",
        body_bytes=12,
        source_job_id=None,
    )

    assert header == "[CCB] job=target=cmd from=agent1 bytes=12 pend=ccb-pend"
    assert CMD_HEADER_RE.match(header)
    assert parse_cmd_header_tokens(header)["job"] == "target=cmd"


def test_header_boundary_math_allows_longest_valid_values() -> None:
    header = preparation_service._prepare_cmd_payload(
        sender_id="a" * 32,
        body_bytes=9_999_999,
        source_job_id="job_" + ("a" * 16),
    )

    assert len(header) == 96
    assert len(header) <= MAX_CMD_HEADER_LEN
    assert CMD_HEADER_RE.match(header)


@pytest.mark.parametrize("body_bytes", [10_000_000, -1])
def test_prepare_cmd_payload_rejects_unrepresentable_bytes(body_bytes: int) -> None:
    with pytest.raises(CmdHeaderValidationError):
        preparation_service._prepare_cmd_payload(
            sender_id="agent1",
            body_bytes=body_bytes,
            source_job_id="job_1234abcd",
        )


@pytest.mark.parametrize("text", ["00", "012", "10000000", "-1"])
def test_header_regex_rejects_invalid_bytes_strings(text: str) -> None:
    header = f"[CCB] job=job_1234abcd from=agent1 bytes={text} pend=ccb-pend"
    assert CMD_HEADER_RE.match(header) is None


def test_parse_cmd_header_tokens_splits_on_first_equal() -> None:
    header = "[CCB] job=target=cmd from=agent1 bytes=0 pend=ccb-pend"

    assert parse_cmd_header_tokens(header) == {
        "job": "target=cmd",
        "from": "agent1",
        "bytes": "0",
        "pend": "ccb-pend",
    }


def test_plan_cmd_delivery_in_header_mode_never_injects_reply_body(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CCB_CMD_DELIVERY_MODE", "header_only")
    reply = _reply(body="this body must stay in mailbox")

    plan, fallback = plan_cmd_delivery(_dispatcher(), reply, project_root=tmp_path, body_store=None)

    assert plan.header_only is True
    assert plan.body_file is None
    assert plan.body.startswith("[CCB] ")
    assert "this body must stay in mailbox" not in plan.body
    assert fallback is None


def test_plan_cmd_delivery_full_body_mode_preserves_legacy_body(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CCB_CMD_DELIVERY_MODE", "full_body")
    reply = _reply(body="legacy body")

    plan, fallback = plan_cmd_delivery(_dispatcher(), reply, project_root=tmp_path, body_store=None)

    assert plan.header_only is False
    assert "legacy body" in plan.body
    assert fallback is None


def test_plan_cmd_delivery_uses_cmd_fallback_when_source_job_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CCB_CMD_DELIVERY_MODE", "header_only")

    plan, _ = plan_cmd_delivery(_dispatcher(job_id=None), _reply(), project_root=tmp_path, body_store=None)

    assert plan.body == "[CCB] job=target=cmd from=agent2 bytes=5 pend=ccb-pend"


def test_body_byte_ceiling_constant_matches_header_contract() -> None:
    assert MAX_CMD_HEADER_BYTES_FIELD == 9_999_999
