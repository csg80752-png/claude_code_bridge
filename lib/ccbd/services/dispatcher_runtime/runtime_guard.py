from __future__ import annotations

from agents.models import AgentState
from ccbd.runtime_failure_policy import runtime_active_actionable_binding_missing
from provider_core.registry import TEST_DOUBLE_PROVIDER_NAMES


def ensure_runtime_deliverable(dispatcher, agent_name: str, runtime) -> None:
    provider = str(getattr(runtime, 'provider', '') or '').strip().lower()
    if provider in TEST_DOUBLE_PROVIDER_NAMES:
        return
    if runtime_active_actionable_binding_missing(runtime):
        raise dispatcher._dispatch_error(
            f'agent {agent_name} has no runtime binding; start the agent before asking it'
        )


def runtime_needs_force_mount(dispatcher, agent_name: str, runtime) -> bool:
    if runtime is None:
        return True
    if runtime.state in {AgentState.STOPPED, AgentState.FAILED}:
        return True
    if runtime.state not in {AgentState.STARTING, AgentState.DEGRADED}:
        return False
    try:
        ensure_runtime_deliverable(dispatcher, agent_name, runtime)
    except dispatcher._dispatch_error:
        return True
    return False


def ensure_dispatch_target_ready(dispatcher, agent_name: str) -> None:
    dispatcher._registry.spec_for(agent_name)
    runtime = dispatcher._registry.get(agent_name)
    if runtime_needs_force_mount(dispatcher, agent_name, runtime):
        if dispatcher._runtime_reconciler is not None:
            dispatcher._runtime_reconciler(agent_name, force_mount=True)
            runtime = dispatcher._registry.get(agent_name)
            if runtime is not None and not runtime_needs_force_mount(dispatcher, agent_name, runtime):
                ensure_runtime_deliverable(dispatcher, agent_name, runtime)
                return
        if dispatcher._runtime_service is None:
            ensure_runtime_deliverable(dispatcher, agent_name, runtime)
            raise dispatcher._dispatch_error(f'agent {agent_name} is not running')
        dispatcher._runtime_service.ensure_ready(agent_name)
        runtime = dispatcher._registry.get(agent_name)
    ensure_runtime_deliverable(dispatcher, agent_name, runtime)


__all__ = ['ensure_dispatch_target_ready', 'ensure_runtime_deliverable', 'runtime_needs_force_mount']
