from __future__ import annotations

import json

from ccbd.services.dispatcher_runtime.reply_delivery_runtime import cmd_delivery_telemetry as telemetry


def test_telemetry_writes_v3_header_inject_success(tmp_path) -> None:
    telemetry.record_cmd_delivery_header_inject_success(
        tmp_path,
        reply_id="rep_1",
        foreground_command="claude",
        delivered_at="2026-05-03T00:00:00Z",
        body_char_count=8,
        delivery_mode="header_only",
        header_only_compatible=True,
    )

    records = telemetry.read_body_read_followup_records(tmp_path)

    assert records == [
        {
            "schema_version": 3,
            "cycle": "v8.3.2",
            "event": "cmd_delivery_header_inject_success",
            "reply_id": "rep_1",
            "foreground_command": "claude",
            "body_char_count": 8,
            "delivered_at": "2026-05-03T00:00:00Z",
            "delivery_mode": "header_only",
            "header_only_compatible": True,
        }
    ]


def test_telemetry_v3_header_error_and_hold_include_delivery_contract_fields(tmp_path) -> None:
    telemetry.record_cmd_delivery_header_inject_error(
        tmp_path,
        reply_id="rep_2",
        foreground_command="claude",
        failed_at="2026-05-03T00:00:01Z",
        body_char_count=9,
        reason="exception",
        delivery_mode="header_only",
        header_only_compatible=True,
    )
    telemetry.record_cmd_delivery_held(
        tmp_path,
        reply_id="rep_3",
        foreground_command="bash",
        held_at="2026-05-03T00:00:02Z",
        body_char_count=10,
        held_reason="not_safe_consumer",
        delivery_mode="full_body",
        header_only_compatible=False,
    )

    records = telemetry.read_body_read_followup_records(tmp_path)

    for record in records:
        assert record["delivery_mode"] in {"header_only", "full_body"}
        assert record["header_only_compatible"] in {True, False}


def test_telemetry_v3_phase2_failure_includes_debug_fields(tmp_path) -> None:
    telemetry.record_phase2_failure(
        tmp_path,
        reply_id="rep_4",
        stage="send",
        reason="exception",
        body_char_count=11,
        failed_at="2026-05-03T00:00:03Z",
        foreground_command="claude",
        pane_alive=True,
        cached=False,
    )

    record = telemetry.read_body_read_followup_records(tmp_path)[0]

    assert record["event"] == "cmd_phase2_failure"
    assert record["foreground_command"] == "claude"
    assert record["pane_alive"] is True
    assert record["cached"] is False


def test_telemetry_reader_tolerates_mixed_v1_v3_and_quarantines_future_schema(tmp_path) -> None:
    path = telemetry.metrics_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                json.dumps({"schema_version": 1, "event": "cmd_delivery_success", "reply_id": "old"}),
                json.dumps({"schema_version": 3, "event": "cmd_delivery_header_inject_error", "reply_id": "new"}),
                json.dumps({"schema_version": 99, "event": "future", "reply_id": "future"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    records = telemetry.read_body_read_followup_records(tmp_path)

    assert [record["reply_id"] for record in records] == ["old", "new"]
    quarantine = path.with_name("body_read_followup.future-schema.jsonl")
    assert quarantine.exists()
    assert "future" in quarantine.read_text(encoding="utf-8")
