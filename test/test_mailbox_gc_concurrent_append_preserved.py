from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from mailbox_kernel import InboundEventRecord, InboundEventStatus, InboundEventStore, InboundEventType
from mailbox_kernel import gc as mailbox_gc
from storage.paths import PathLayout


def _event(event_id: str) -> InboundEventRecord:
    return InboundEventRecord(
        inbound_event_id=event_id,
        agent_name="cmd",
        event_type=InboundEventType.TASK_REPLY,
        message_id=f"msg-{event_id}",
        attempt_id=f"att-{event_id}",
        payload_ref=f"reply:{event_id}",
        priority=10,
        status=InboundEventStatus.QUEUED,
        created_at=f"2026-05-03T00:00:0{event_id[-1]}Z",
    )


def test_mailbox_gc_preserves_append_between_compaction_read_and_rewrite(monkeypatch, tmp_path: Path) -> None:
    layout = PathLayout(tmp_path / "repo")
    store = InboundEventStore(layout)
    store.append(_event("evt_1"))
    appended = False

    original_guard = mailbox_gc.guarded_state_mutation_for_path

    def _append_json_row(path, row):
        import json

        with Path(path).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    @contextmanager
    def _guard_with_append(path, **kwargs):
        nonlocal appended
        with original_guard(path, **kwargs):
            if Path(path) == layout.agent_inbox_path("cmd") and not appended:
                appended = True
                _append_json_row(path, _event("evt_2").to_record())
            yield

    monkeypatch.setattr(mailbox_gc, "guarded_state_mutation_for_path", _guard_with_append)

    mailbox_gc.compact_mailbox_jsonl(layout, agent_names=("cmd",), now="2026-05-03T00:00:10Z")

    event_ids = [record.inbound_event_id for record in InboundEventStore(layout).list_agent("cmd")]
    assert event_ids == ["evt_1", "evt_2"]
