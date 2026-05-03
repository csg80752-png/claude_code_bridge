from __future__ import annotations

from pathlib import Path

from mailbox_kernel import InboundEventRecord, InboundEventStatus, InboundEventStore, InboundEventType
from mailbox_kernel.gc import compact_mailbox_jsonl
from storage.paths import PathLayout


def _event(event_id: str, *, status: InboundEventStatus, created_at: str, reply_id: str | None = None):
    return InboundEventRecord(
        inbound_event_id=event_id,
        agent_name="cmd",
        event_type=InboundEventType.TASK_REPLY,
        message_id=f"msg-{event_id}",
        attempt_id=f"att-{event_id}",
        payload_ref=f"reply:{reply_id or event_id}",
        priority=10,
        status=status,
        created_at=created_at,
        finished_at=created_at if status is InboundEventStatus.CONSUMED else None,
    )


def test_mailbox_gc_drops_old_terminal_tail_after_preserving_pending(tmp_path: Path) -> None:
    layout = PathLayout(tmp_path / "repo")
    store = InboundEventStore(layout)
    store.append(_event("evt_old", status=InboundEventStatus.CONSUMED, created_at="2026-04-01T00:00:00Z"))
    store.append(_event("evt_pending", status=InboundEventStatus.QUEUED, created_at="2026-05-03T00:00:00Z"))

    compact_mailbox_jsonl(
        layout,
        agent_names=("cmd",),
        now="2026-05-03T00:00:00Z",
        max_pending_age_days=7,
    )

    assert [event.inbound_event_id for event in InboundEventStore(layout).list_agent("cmd")] == ["evt_pending"]


def test_mailbox_gc_size_limit_drops_terminal_rows_before_pending_rows(tmp_path: Path) -> None:
    layout = PathLayout(tmp_path / "repo")
    store = InboundEventStore(layout)
    for index in range(20):
        store.append(
            _event(
                f"evt_done_{index}",
                status=InboundEventStatus.CONSUMED,
                created_at=f"2026-05-03T00:00:{index:02d}Z",
            )
        )
    store.append(_event("evt_pending", status=InboundEventStatus.QUEUED, created_at="2026-05-03T00:01:00Z"))

    compact_mailbox_jsonl(
        layout,
        agent_names=("cmd",),
        now="2026-05-03T00:02:00Z",
        max_bytes_per_mailbox=800,
    )

    event_ids = [event.inbound_event_id for event in InboundEventStore(layout).list_agent("cmd")]
    assert "evt_pending" in event_ids
    assert len(event_ids) < 21
