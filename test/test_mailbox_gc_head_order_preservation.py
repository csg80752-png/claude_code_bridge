from __future__ import annotations

from pathlib import Path

from mailbox_kernel import InboundEventRecord, InboundEventStatus, InboundEventStore, InboundEventType, MailboxKernelService
from mailbox_kernel.gc import compact_mailbox_jsonl
from storage.paths import PathLayout


def _event(event_id: str, *, status: InboundEventStatus = InboundEventStatus.QUEUED, reply_id: str | None = None):
    return InboundEventRecord(
        inbound_event_id=event_id,
        agent_name="cmd",
        event_type=InboundEventType.TASK_REPLY,
        message_id=f"msg-{event_id}",
        attempt_id=f"att-{event_id}",
        payload_ref=f"reply:{reply_id or event_id}",
        priority=10,
        status=status,
        created_at=f"2026-05-03T00:00:0{event_id[-1]}Z",
        finished_at="2026-05-03T00:00:09Z" if status is InboundEventStatus.CONSUMED else None,
    )


def _service(layout: PathLayout, store: InboundEventStore) -> MailboxKernelService:
    return MailboxKernelService(layout, clock=lambda: "2026-05-03T00:00:10Z", inbound_store=store)


def test_mailbox_gc_preserves_cmd_head_order_across_compaction(tmp_path: Path) -> None:
    layout = PathLayout(tmp_path / "repo")
    store = InboundEventStore(layout)
    store.append(_event("evt_1", reply_id="rep_1"))
    store.append(_event("evt_2", reply_id="rep_2"))
    store.append(_event("evt_old", status=InboundEventStatus.CONSUMED, reply_id="rep_old"))
    before = _service(layout, store).head_pending_event("cmd")

    compact_mailbox_jsonl(layout, agent_names=("cmd",), now="2026-05-03T00:00:10Z")

    after_store = InboundEventStore(layout)
    after = _service(layout, after_store).head_pending_event("cmd")
    pending_ids = [event.inbound_event_id for event in _service(layout, after_store).pending_events("cmd")]

    assert before is not None
    assert after is not None
    assert after.inbound_event_id == before.inbound_event_id == "evt_1"
    assert pending_ids[:2] == ["evt_1", "evt_2"]

