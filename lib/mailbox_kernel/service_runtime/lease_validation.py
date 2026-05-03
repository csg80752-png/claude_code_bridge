from __future__ import annotations

LEASE_STATUS_INACTIVE = 'inactive'
LEASE_STATUS_ORPHAN = 'orphan'
LEASE_STATUS_ACTIVE = 'active'


def classify_lease(service, lease) -> str:
    """Classify a lease without mutating storage.

    Returns one of:

    - ``'inactive'`` — lease is missing or not in the acquired state.
    - ``'orphan'`` — lease is acquired but the inbound event it references
      is missing or already terminal. Callers should drop it; while it sits
      on disk it must not be treated as DELIVERING evidence.
    - ``'active'`` — lease is acquired and references a live, non-terminal
      event. Safe to use as DELIVERING evidence.

    The function is pure; it has no side effects. ``refresh_mailbox`` and the
    claim path each decide independently when to remove an orphan or to let
    ``next_lease_version`` overwrite it on the next claim.
    """
    if lease is None or lease.lease_state is not service._lease_state_acquired:
        return LEASE_STATUS_INACTIVE
    event = service._inbound_store.get_latest(lease.agent_name, lease.inbound_event_id)
    if event is None or event.status in service._terminal_event_states:
        return LEASE_STATUS_ORPHAN
    return LEASE_STATUS_ACTIVE


__all__ = [
    'LEASE_STATUS_ACTIVE',
    'LEASE_STATUS_INACTIVE',
    'LEASE_STATUS_ORPHAN',
    'classify_lease',
]
