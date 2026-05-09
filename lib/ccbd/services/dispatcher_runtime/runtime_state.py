from __future__ import annotations

from dataclasses import replace
from typing import Literal

from agents.models import AgentState, RuntimeBindingSource, normalize_runtime_binding_source

BindingState = Literal['bound', 'pane-dead', 'pane-missing', 'unknown']


def _is_pane_backed(runtime) -> bool:
    try:
        binding_source = normalize_runtime_binding_source(runtime.binding_source)
    except Exception:
        binding_source = RuntimeBindingSource.PROVIDER_SESSION
    return binding_source is RuntimeBindingSource.PROVIDER_SESSION and bool(
        str(getattr(runtime, 'terminal_backend', None) or '').strip()
    )


def _is_in_transition(runtime) -> bool:
    return getattr(runtime, 'state', None) in {AgentState.STARTING, AgentState.STOPPING} or str(
        getattr(runtime, 'reconcile_state', '') or ''
    ).strip() == 'starting'


def _classify_binding(runtime) -> BindingState:
    pane_state = str(getattr(runtime, 'pane_state', None) or '').strip().lower()
    if pane_state == 'missing':
        return 'pane-missing'
    if pane_state in {'dead', 'shell'}:
        return 'pane-dead'
    if pane_state == 'alive':
        return 'bound'
    active_pane_id = str(getattr(runtime, 'active_pane_id', None) or '').strip()
    if active_pane_id:
        return 'bound'
    if pane_state == 'unknown':
        return 'unknown'
    return 'unknown'


def _has_resumable_active_execution(active_job: str | None, runtime) -> bool:
    return active_job is not None and _classify_binding(runtime) == 'bound'


def _guard_stale_busy(
    runtime,
    *,
    active_job: str | None,
    next_state: AgentState,
    queue_depth: int,
) -> tuple[AgentState, bool]:
    if next_state is not AgentState.BUSY and getattr(runtime, 'state', None) is not AgentState.BUSY:
        return next_state, False
    if not _is_pane_backed(runtime) or _is_in_transition(runtime):
        return next_state, False
    binding = _classify_binding(runtime)
    if binding == 'unknown' and getattr(runtime, 'state', None) is AgentState.BUSY:
        return AgentState.BUSY, False
    if binding not in {'pane-dead', 'pane-missing'}:
        return next_state, False
    if _has_resumable_active_execution(active_job, runtime):
        return next_state, False
    return AgentState.DEGRADED if queue_depth > 0 else AgentState.IDLE, True


def sync_runtime(dispatcher, agent_name: str, *, state: AgentState | None = None) -> None:
    runtime = dispatcher._registry.get(agent_name)
    if runtime is None:
        return
    next_state = state
    active_job = dispatcher._state.active_job(agent_name)
    queue_depth = dispatcher._state.queue_depth(agent_name)
    if next_state is None:
        if active_job is not None:
            next_state = AgentState.BUSY
        elif runtime.state is AgentState.BUSY:
            next_state = AgentState.IDLE
        else:
            next_state = runtime.state
    if next_state is AgentState.BUSY and runtime.state is AgentState.STOPPED:
        next_state = AgentState.IDLE
    guarded_state, demoted_stale_busy = _guard_stale_busy(
        runtime,
        active_job=active_job,
        next_state=next_state,
        queue_depth=queue_depth,
    )
    last_failure_reason = runtime.last_failure_reason
    if demoted_stale_busy:
        last_failure_reason = 'stale-busy-demoted'
    next_state = guarded_state
    updated = replace(
        runtime,
        state=next_state,
        queue_depth=queue_depth,
        last_seen_at=dispatcher._clock(),
        last_failure_reason=last_failure_reason,
    )
    dispatcher._registry.upsert(updated)


__all__ = ['_classify_binding', '_is_pane_backed', 'sync_runtime']
