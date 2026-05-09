from __future__ import annotations

from agents.models import AgentState, RuntimeMode
from provider_core.registry import TEST_DOUBLE_PROVIDER_NAMES

UNBOUND_MOUNT_FAILURE_REASON = 'mount-produced-unbound-runtime'
BINDING_FAILURE_REASONS = frozenset(
    {
        UNBOUND_MOUNT_FAILURE_REASON,
        'binding_missing_after_launch',
        'partial_binding_unresolved',
        'stale_binding_unresolved',
    }
)
ACTIONABLE_BINDING_FIELDS = ('runtime_ref', 'session_ref')
BINDING_EVIDENCE_FIELDS = ('runtime_ref', 'session_ref', 'pane_id', 'active_pane_id')


def runtime_has_unbound_mount_failure(runtime) -> bool:
    if runtime is None:
        return False
    reason = str(getattr(runtime, 'last_failure_reason', '') or '').strip()
    return reason == UNBOUND_MOUNT_FAILURE_REASON


def runtime_auto_start_blocked(runtime) -> bool:
    if runtime_has_unbound_mount_failure(runtime):
        return True
    return runtime_active_binding_missing(runtime)


def runtime_binding_missing(runtime) -> bool:
    for field_name in BINDING_EVIDENCE_FIELDS:
        if str(getattr(runtime, field_name, None) or '').strip():
            return False
    return True


def runtime_actionable_binding_missing(runtime) -> bool:
    return not runtime_has_actionable_binding(runtime)


def runtime_has_actionable_binding(runtime) -> bool:
    return all(
        str(getattr(runtime, field_name, None) or '').strip()
        for field_name in ACTIONABLE_BINDING_FIELDS
    )


def runtime_mode_requires_binding(runtime) -> bool:
    provider = str(getattr(runtime, 'provider', '') or '').strip().lower()
    if provider in TEST_DOUBLE_PROVIDER_NAMES:
        return False
    backend_type = str(getattr(runtime, 'backend_type', None) or '').strip()
    return backend_type not in {RuntimeMode.HEADLESS.value, RuntimeMode.PTY_BACKED.value}


def runtime_requires_binding_and_is_unbound(runtime) -> bool:
    return runtime is not None and runtime_mode_requires_binding(runtime) and runtime_binding_missing(runtime)


def runtime_requires_actionable_binding_and_is_missing(runtime) -> bool:
    return runtime is not None and runtime_mode_requires_binding(runtime) and runtime_actionable_binding_missing(runtime)


def runtime_active_binding_missing(runtime) -> bool:
    if not runtime_requires_binding_and_is_unbound(runtime):
        return False
    return getattr(runtime, 'state', None) in {AgentState.IDLE, AgentState.BUSY, AgentState.STARTING}


def runtime_active_actionable_binding_missing(runtime) -> bool:
    if not runtime_requires_actionable_binding_and_is_missing(runtime):
        return False
    state = getattr(runtime, 'state', None)
    if state in {AgentState.IDLE, AgentState.BUSY, AgentState.STARTING}:
        return True
    if state is not AgentState.DEGRADED:
        return False
    reason = str(getattr(runtime, 'last_failure_reason', '') or '').strip()
    health = str(getattr(runtime, 'health', '') or '').strip()
    return reason in BINDING_FAILURE_REASONS or health == 'degraded'


def preserved_stop_failure_reason(runtime) -> str | None:
    if not runtime_auto_start_blocked(runtime):
        return None
    return str(getattr(runtime, 'last_failure_reason', '') or '') or UNBOUND_MOUNT_FAILURE_REASON


__all__ = [
    'ACTIONABLE_BINDING_FIELDS',
    'BINDING_FAILURE_REASONS',
    'BINDING_EVIDENCE_FIELDS',
    'UNBOUND_MOUNT_FAILURE_REASON',
    'preserved_stop_failure_reason',
    'runtime_actionable_binding_missing',
    'runtime_auto_start_blocked',
    'runtime_active_actionable_binding_missing',
    'runtime_active_binding_missing',
    'runtime_binding_missing',
    'runtime_has_actionable_binding',
    'runtime_has_unbound_mount_failure',
    'runtime_mode_requires_binding',
    'runtime_requires_actionable_binding_and_is_missing',
    'runtime_requires_binding_and_is_unbound',
]
