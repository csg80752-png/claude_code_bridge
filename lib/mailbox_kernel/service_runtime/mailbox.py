from __future__ import annotations

from mailbox_runtime.targets import normalize_mailbox_owner_name

from .lease_validation import LEASE_STATUS_ACTIVE, LEASE_STATUS_ORPHAN, classify_lease
from .queries import latest_events, pending_events


def refresh_mailbox(service, agent_name: str, *, updated_at: str | None = None):
    normalized = normalize_mailbox_owner_name(agent_name)
    timestamp = updated_at or service._clock()
    prior = service._mailbox_store.load(normalized)
    lease = service._lease_store.load(normalized)
    lease_status = classify_lease(service, lease)
    if lease_status is LEASE_STATUS_ORPHAN:
        service._lease_store.remove(normalized)
    events = pending_events(service, normalized)
    queue_depth = len(events)
    pending_reply_count = sum(1 for event in events if event.event_type is service._reply_event_type)
    mailbox_state, active_inbound_event_id, lease_version = _mailbox_facts(
        prior=prior,
        lease=lease,
        lease_status=lease_status,
        queue_depth=queue_depth,
        service=service,
    )
    last_started, last_finished = _latest_activity(
        service,
        normalized,
        prior=prior,
    )

    record = service._mailbox_record_cls(
        mailbox_id=prior.mailbox_id if prior is not None else f'mbx_{normalized}',
        agent_name=normalized,
        active_inbound_event_id=active_inbound_event_id,
        queue_depth=queue_depth,
        pending_reply_count=pending_reply_count,
        last_inbound_started_at=last_started,
        last_inbound_finished_at=last_finished,
        mailbox_state=mailbox_state,
        lease_version=lease_version,
        updated_at=timestamp,
    )
    service._mailbox_store.save(record)
    return record


def _mailbox_facts(*, prior, lease, lease_status: str, queue_depth: int, service):
    if lease_status is LEASE_STATUS_ACTIVE:
        return (
            service._mailbox_state_delivering,
            lease.inbound_event_id,
            lease.lease_version,
        )
    fallback_version = _fallback_lease_version(prior, lease, lease_status)
    if queue_depth > 0:
        return (
            service._mailbox_state_blocked,
            None,
            fallback_version,
        )
    return (
        service._mailbox_state_idle,
        None,
        fallback_version,
    )


def _fallback_lease_version(prior, lease, lease_status: str) -> int:
    prior_version = _prior_lease_version(prior)
    if lease_status is LEASE_STATUS_ORPHAN and lease is not None:
        return max(prior_version, lease.lease_version)
    return prior_version


def _prior_lease_version(prior) -> int:
    return prior.lease_version if prior is not None else 0


def _latest_activity(service, normalized: str, *, prior):
    last_started = prior.last_inbound_started_at if prior is not None else None
    last_finished = prior.last_inbound_finished_at if prior is not None else None
    for event in latest_events(service, normalized):
        last_started = _latest_timestamp(last_started, event.started_at)
        last_finished = _latest_timestamp(last_finished, event.finished_at)
    return last_started, last_finished


def _latest_timestamp(current: str | None, candidate: str | None) -> str | None:
    if candidate and (current is None or candidate > current):
        return candidate
    return current


__all__ = ['refresh_mailbox']
