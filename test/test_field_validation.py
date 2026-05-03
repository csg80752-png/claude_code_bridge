from __future__ import annotations

import pytest

from ccbd.services.dispatcher_runtime.reply_delivery_runtime.cmd_transport_planner import (
    CMD_HEADER_RE,
    CmdHeaderValidationError,
    parse_cmd_header_tokens,
    prepare_cmd_payload,
)


@pytest.mark.parametrize("sender_id", ["Agent", "agent.name", "agent;rm", "agent\nx", "", "a" * 33])
def test_header_field_validation_rejects_invalid_agent_name(sender_id: str) -> None:
    with pytest.raises(CmdHeaderValidationError):
        prepare_cmd_payload(sender_id=sender_id, body_bytes=1, source_job_id="job_1234abcd")


@pytest.mark.parametrize("source_job_id", ["job_1234abc", "job_1234abcdzz", "not-a-job", "job_1234abcd;rm"])
def test_header_field_validation_falls_back_for_invalid_job_id(source_job_id: str) -> None:
    header = prepare_cmd_payload(sender_id="agent1", body_bytes=1, source_job_id=source_job_id)

    assert parse_cmd_header_tokens(header)["job"] == "target=cmd"


@pytest.mark.parametrize("body_bytes", [-1, 10_000_000, "bad"])
def test_header_field_validation_rejects_invalid_body_bytes(body_bytes) -> None:
    with pytest.raises(CmdHeaderValidationError):
        prepare_cmd_payload(sender_id="agent1", body_bytes=body_bytes, source_job_id="job_1234abcd")


def test_header_field_validation_accepts_maximal_header_contract() -> None:
    header = prepare_cmd_payload(sender_id="a" * 32, body_bytes=9_999_999, source_job_id="job_" + "1" * 16)

    assert CMD_HEADER_RE.fullmatch(header)
    assert len(header) == 96
