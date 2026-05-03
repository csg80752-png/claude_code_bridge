from __future__ import annotations

from pathlib import Path

from mailbox_kernel import (
    DeliveryLease,
    DeliveryLeaseStore,
    InboundEventRecord,
    InboundEventStatus,
    InboundEventStore,
    InboundEventType,
    LeaseState,
    MailboxKernelService,
    MailboxState,
    MailboxStore,
)
from storage.paths import PathLayout


def _make_service(tmp_path: Path) -> tuple[MailboxKernelService, InboundEventStore, MailboxStore, DeliveryLeaseStore]:
    layout = PathLayout(tmp_path / 'repo')
    inbound_store = InboundEventStore(layout)
    mailbox_store = MailboxStore(layout)
    lease_store = DeliveryLeaseStore(layout)
    service = MailboxKernelService(
        layout,
        clock=lambda: '2026-05-04T03:00:00Z',
        inbound_store=inbound_store,
        mailbox_store=mailbox_store,
        lease_store=lease_store,
    )
    return service, inbound_store, mailbox_store, lease_store


def _seed_orphan_lease(
    inbound_store: InboundEventStore,
    lease_store: DeliveryLeaseStore,
    *,
    agent: str,
    event_id: str,
    terminal_status: InboundEventStatus = InboundEventStatus.CONSUMED,
) -> None:
    inbound_store.append(
        InboundEventRecord(
            inbound_event_id=event_id,
            agent_name=agent,
            event_type=InboundEventType.TASK_REQUEST,
            message_id='msg-' + event_id,
            attempt_id='att-' + event_id,
            payload_ref='job:' + event_id,
            priority=100,
            status=InboundEventStatus.QUEUED,
            created_at='2026-05-04T02:00:00Z',
        )
    )
    inbound_store.append(
        InboundEventRecord(
            inbound_event_id=event_id,
            agent_name=agent,
            event_type=InboundEventType.TASK_REQUEST,
            message_id='msg-' + event_id,
            attempt_id='att-' + event_id,
            payload_ref='job:' + event_id,
            priority=100,
            status=terminal_status,
            created_at='2026-05-04T02:00:00Z',
            started_at='2026-05-04T02:00:01Z',
            finished_at='2026-05-04T02:00:05Z',
        )
    )
    lease_store.save(
        DeliveryLease(
            agent_name=agent,
            inbound_event_id=event_id,
            lease_version=7,
            acquired_at='2026-05-04T02:00:01Z',
            last_progress_at='2026-05-04T02:00:01Z',
            expires_at=None,
            lease_state=LeaseState.ACQUIRED,
        )
    )


def test_orphan_lease_referencing_consumed_event_drops_on_refresh(tmp_path: Path) -> None:
    service, inbound_store, mailbox_store, lease_store = _make_service(tmp_path)
    _seed_orphan_lease(inbound_store, lease_store, agent='agent1', event_id='evt-stale')

    mailbox = service.refresh_mailbox('agent1')

    assert lease_store.load('agent1') is None
    assert mailbox.mailbox_state is MailboxState.IDLE
    assert mailbox.active_inbound_event_id is None


def test_orphan_lease_referencing_abandoned_event_drops_on_refresh(tmp_path: Path) -> None:
    service, inbound_store, mailbox_store, lease_store = _make_service(tmp_path)
    _seed_orphan_lease(
        inbound_store,
        lease_store,
        agent='agent1',
        event_id='evt-abandoned',
        terminal_status=InboundEventStatus.ABANDONED,
    )

    mailbox = service.refresh_mailbox('agent1')

    assert lease_store.load('agent1') is None
    assert mailbox.mailbox_state is MailboxState.IDLE
    assert mailbox.active_inbound_event_id is None


def test_orphan_lease_with_missing_event_drops_on_refresh(tmp_path: Path) -> None:
    service, inbound_store, mailbox_store, lease_store = _make_service(tmp_path)
    lease_store.save(
        DeliveryLease(
            agent_name='agent1',
            inbound_event_id='evt-missing',
            lease_version=3,
            acquired_at='2026-05-04T01:00:00Z',
            last_progress_at='2026-05-04T01:00:00Z',
            expires_at=None,
            lease_state=LeaseState.ACQUIRED,
        )
    )

    mailbox = service.refresh_mailbox('agent1')

    assert lease_store.load('agent1') is None
    assert mailbox.mailbox_state is MailboxState.IDLE
    assert mailbox.active_inbound_event_id is None


def test_orphan_lease_dropped_but_other_pending_events_keep_mailbox_blocked(tmp_path: Path) -> None:
    service, inbound_store, mailbox_store, lease_store = _make_service(tmp_path)
    _seed_orphan_lease(inbound_store, lease_store, agent='agent1', event_id='evt-stale')
    inbound_store.append(
        InboundEventRecord(
            inbound_event_id='evt-fresh',
            agent_name='agent1',
            event_type=InboundEventType.TASK_REQUEST,
            message_id='msg-fresh',
            attempt_id='att-fresh',
            payload_ref='job:fresh',
            priority=100,
            status=InboundEventStatus.QUEUED,
            created_at='2026-05-04T02:30:00Z',
        )
    )

    mailbox = service.refresh_mailbox('agent1')

    assert lease_store.load('agent1') is None
    assert mailbox.mailbox_state is MailboxState.BLOCKED
    assert mailbox.active_inbound_event_id is None
    assert mailbox.queue_depth == 1


def test_acquired_lease_for_live_delivering_event_preserved(tmp_path: Path) -> None:
    service, inbound_store, mailbox_store, lease_store = _make_service(tmp_path)
    inbound_store.append(
        InboundEventRecord(
            inbound_event_id='evt-live',
            agent_name='agent1',
            event_type=InboundEventType.TASK_REQUEST,
            message_id='msg-live',
            attempt_id='att-live',
            payload_ref='job:live',
            priority=100,
            status=InboundEventStatus.QUEUED,
            created_at='2026-05-04T02:00:00Z',
        )
    )
    claimed = service.claim_next('agent1', event_type=InboundEventType.TASK_REQUEST, started_at='2026-05-04T02:00:01Z')
    assert claimed is not None
    assert claimed.status is InboundEventStatus.DELIVERING

    mailbox = service.refresh_mailbox('agent1')

    lease = lease_store.load('agent1')
    assert lease is not None
    assert lease.inbound_event_id == 'evt-live'
    assert lease.lease_state is LeaseState.ACQUIRED
    assert mailbox.mailbox_state is MailboxState.DELIVERING
    assert mailbox.active_inbound_event_id == 'evt-live'


def test_orphan_lease_recovers_across_kernel_restart(tmp_path: Path) -> None:
    layout = PathLayout(tmp_path / 'repo')
    inbound_store_a = InboundEventStore(layout)
    mailbox_store_a = MailboxStore(layout)
    lease_store_a = DeliveryLeaseStore(layout)
    _seed_orphan_lease(inbound_store_a, lease_store_a, agent='agent1', event_id='evt-stale')
    assert lease_store_a.load('agent1') is not None

    inbound_store_b = InboundEventStore(layout)
    mailbox_store_b = MailboxStore(layout)
    lease_store_b = DeliveryLeaseStore(layout)
    service_b = MailboxKernelService(
        layout,
        clock=lambda: '2026-05-04T03:00:00Z',
        inbound_store=inbound_store_b,
        mailbox_store=mailbox_store_b,
        lease_store=lease_store_b,
    )

    mailbox = service_b.refresh_mailbox('agent1')

    assert lease_store_b.load('agent1') is None
    assert mailbox.mailbox_state is MailboxState.IDLE
    assert mailbox.active_inbound_event_id is None
