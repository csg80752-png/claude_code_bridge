from pathlib import Path

from mailbox_kernel import InboundEventRecord, InboundEventStatus, InboundEventStore, InboundEventType, MailboxKernelService
from storage.paths import PathLayout


def _event(event_id, status, created_at):
    return InboundEventRecord(
        inbound_event_id=event_id,
        agent_name='agent1',
        event_type=InboundEventType.TASK_REPLY,
        message_id=f'msg-{event_id}',
        attempt_id=f'attempt-{event_id}',
        payload_ref=f'reply:{event_id}',
        priority=10,
        status=status,
        created_at=created_at,
        finished_at='2026-04-30T00:00:03Z' if status in (InboundEventStatus.CONSUMED, InboundEventStatus.SUPERSEDED, InboundEventStatus.ABANDONED) else None,
    )


def _snapshot(tmp_path, monkeypatch, cache_enabled):
    # v8.1+ ships READAMP_CACHE default-ON, so an unset env evaluates to True.
    # Disabling the cache for parity comparison requires an explicit '0'.
    if cache_enabled:
        monkeypatch.setenv('CCB_CCBD_READAMP_CACHE', '1')
    else:
        monkeypatch.setenv('CCB_CCBD_READAMP_CACHE', '0')
    layout = PathLayout(Path(tmp_path) / 'repo')
    store = InboundEventStore(layout)
    service = MailboxKernelService(layout, clock=lambda: '2026-04-30T00:00:00Z', inbound_store=store)
    store.append(_event('evt-1', InboundEventStatus.QUEUED, '2026-04-30T00:00:00Z'))
    store.append(_event('evt-2', InboundEventStatus.QUEUED, '2026-04-30T00:00:01Z'))
    store.append(_event('evt-1', InboundEventStatus.CONSUMED, '2026-04-30T00:00:02Z'))
    latest = [(event.inbound_event_id, event.status.value) for event in service.latest_events('agent1')]
    pending = [(event.inbound_event_id, event.status.value) for event in service.pending_events('agent1')]
    head = service.head_pending_event('agent1')
    return latest, pending, None if head is None else (head.inbound_event_id, head.status.value)


def test_head_pending_latest_and_terminal_overlay_match_cache_on_off_parity(monkeypatch, tmp_path):
    off = _snapshot(tmp_path / 'off', monkeypatch, cache_enabled=False)
    on = _snapshot(tmp_path / 'on', monkeypatch, cache_enabled=True)
    assert on == off
    assert off[0] == [('evt-1', 'consumed'), ('evt-2', 'queued')]
    assert off[1] == [('evt-2', 'queued')]
    assert off[2] == ('evt-2', 'queued')
