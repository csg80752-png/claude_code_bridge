from __future__ import annotations

import json
from pathlib import Path

from mailbox_kernel import InboundEventRecord, InboundEventStatus, InboundEventStore, InboundEventType
from mailbox_kernel import gc as mailbox_gc
from storage.paths import PathLayout


def _event(event_id: str, reply_id: str) -> InboundEventRecord:
    return InboundEventRecord(
        inbound_event_id=event_id,
        agent_name="cmd",
        event_type=InboundEventType.TASK_REPLY,
        message_id=f"msg-{event_id}",
        attempt_id=f"att-{event_id}",
        payload_ref=f"reply:{reply_id}",
        priority=10,
        status=InboundEventStatus.QUEUED,
        created_at="2026-05-03T00:00:00Z",
    )


def _append_json(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _reply(reply_id: str) -> dict:
    return {
        "reply_id": reply_id,
        "message_id": f"msg-{reply_id}",
        "attempt_id": f"att-{reply_id}",
        "agent_name": "agent1",
        "terminal_status": "completed",
        "reply": "x" * 200,
        "diagnostics": {},
        "finished_at": "2026-05-03T00:00:00Z",
    }


def test_mailbox_gc_preserves_reply_for_pending_head_added_between_inbox_and_reply_compaction(
    monkeypatch,
    tmp_path: Path,
) -> None:
    layout = PathLayout(tmp_path / "repo")
    InboundEventStore(layout).append(_event("evt_existing", "rep_existing"))
    _append_json(layout.ccbd_replies_path, _reply("rep_existing"))
    _append_json(layout.ccbd_replies_path, _reply("rep_race"))
    original_compact = mailbox_gc._compact_jsonl_file_with_locked_transform
    appended = False

    def _compact_then_append_pending(path, **kwargs):
        nonlocal appended
        compacted = original_compact(path, **kwargs)
        if Path(path) == layout.agent_inbox_path("cmd") and not appended:
            appended = True
            _append_json(path, _event("evt_race", "rep_race").to_record())
        return compacted

    monkeypatch.setattr(mailbox_gc, "_compact_jsonl_file_with_locked_transform", _compact_then_append_pending)

    mailbox_gc.compact_mailbox_jsonl(
        layout,
        agent_names=("cmd",),
        now="2026-05-03T00:00:10Z",
        max_bytes_per_mailbox=1,
    )

    reply_ids = {
        json.loads(line)["reply_id"]
        for line in layout.ccbd_replies_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    assert "rep_race" in reply_ids
