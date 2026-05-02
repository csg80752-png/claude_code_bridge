from pathlib import Path

from mailbox_kernel import InboundEventRecord, InboundEventStatus, InboundEventStore, InboundEventType, MailboxKernelService
from storage.paths import PathLayout


def _event(status=InboundEventStatus.QUEUED):
    return InboundEventRecord(
        inbound_event_id='evt-claim',
        agent_name='agent1',
        event_type=InboundEventType.TASK_REPLY,
        message_id='msg-1',
        attempt_id='attempt-1',
        payload_ref='reply:reply-1',
        priority=10,
        status=status,
        created_at='2026-04-30T00:00:00Z',
    )


def test_claim_then_terminal_sequence_observes_cache_appends(monkeypatch, tmp_path):
    monkeypatch.setenv('CCB_CCBD_READAMP_CACHE', '1')
    layout = PathLayout(Path(tmp_path) / 'repo')
    inbound_store = InboundEventStore(layout)
    inbound_store.append(_event())
    service = MailboxKernelService(layout, clock=lambda: '2026-04-30T00:00:00Z', inbound_store=inbound_store)

    assert service.head_pending_event('agent1').inbound_event_id == 'evt-claim'

    claimed = service.claim('agent1', 'evt-claim', started_at='2026-04-30T00:00:01Z')
    assert claimed.status == InboundEventStatus.DELIVERING
    assert service.head_pending_event('agent1').status == InboundEventStatus.DELIVERING

    terminal = service.ack_reply(
        'agent1',
        'evt-claim',
        started_at='2026-04-30T00:00:01Z',
        finished_at='2026-04-30T00:00:02Z',
    )
    assert terminal.status == InboundEventStatus.CONSUMED
    assert service.head_pending_event('agent1') is None
    assert inbound_store.get_latest('agent1', 'evt-claim').status == InboundEventStatus.CONSUMED
