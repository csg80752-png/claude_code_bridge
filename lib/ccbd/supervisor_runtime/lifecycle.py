from __future__ import annotations

from agents.models import build_project_layout_plan
from ccbd.models import CcbdStartupAgentResult
from ccbd.runtime_failure_policy import UNBOUND_MOUNT_FAILURE_REASON, runtime_auto_start_blocked

from .namespace import ensure_project_namespace
from .reporting import record_shutdown_report, record_startup_report


def start_supervisor(
    supervisor,
    *,
    agent_names: tuple[str, ...],
    restore: bool,
    auto_permission: bool,
    cleanup_tmux_orphans: bool,
    interactive_tmux_layout: bool,
    recreate_namespace: bool,
    reflow_workspace: bool,
    recreate_reason: str | None,
    skip_auto_start_blocked: bool = False,
    run_start_flow_fn,
):
    skipped_agent_results: tuple[CcbdStartupAgentResult, ...] = ()
    try:
        target_agent_names, skipped_agent_results = start_targets(
            supervisor,
            agent_names,
            skip_auto_start_blocked=skip_auto_start_blocked,
        )
        start_flow_kwargs = _start_flow_kwargs(
            supervisor,
            agent_names=agent_names,
            target_agent_names=target_agent_names,
            skipped_agent_results=skipped_agent_results,
            restore=restore,
            auto_permission=auto_permission,
            cleanup_tmux_orphans=cleanup_tmux_orphans,
            interactive_tmux_layout=interactive_tmux_layout,
        )
        if target_agent_names == () and not bool(getattr(supervisor._config, 'cmd_enabled', False)):
            summary = run_start_flow_fn(
                **start_flow_kwargs,
                tmux_socket_path=None,
                tmux_session_name=None,
                tmux_workspace_window_name=None,
                namespace_epoch=None,
                workspace_window_id=None,
                workspace_epoch=None,
                fresh_namespace=False,
                fresh_workspace=False,
            )
        else:
            namespace_layout_signature = (
                build_project_layout_plan(
                    supervisor._config,
                    requested_agents=agent_names,
                ).signature
                if supervisor._project_namespace is not None
                and interactive_tmux_layout
                else None
            )
            namespace = (
                ensure_project_namespace(
                    supervisor._project_namespace,
                    layout_signature=namespace_layout_signature,
                    recreate_namespace=recreate_namespace,
                    reflow_workspace=reflow_workspace,
                    recreate_reason=recreate_reason,
                )
                if supervisor._project_namespace is not None
                else None
            )
            summary = run_start_flow_fn(
                **start_flow_kwargs,
                tmux_socket_path=namespace.tmux_socket_path if namespace is not None else None,
                tmux_session_name=namespace.tmux_session_name if namespace is not None else None,
                tmux_workspace_window_name=getattr(namespace, 'workspace_window_name', None) if namespace is not None else None,
                namespace_epoch=namespace.namespace_epoch if namespace is not None else None,
                workspace_window_id=getattr(namespace, 'workspace_window_id', None) if namespace is not None else None,
                workspace_epoch=getattr(namespace, 'workspace_epoch', None) if namespace is not None else None,
                fresh_namespace=bool(getattr(namespace, 'created_this_call', False)),
                fresh_workspace=bool(getattr(namespace, 'workspace_recreated_this_call', False)),
            )
    except Exception as exc:
        record_startup_report(
            supervisor,
            requested_agents=agent_names,
            restore=restore,
            auto_permission=auto_permission,
            status='failed',
            actions_taken=('start_flow_failed',),
            cleanup_summaries=(),
            agent_results=skipped_agent_results,
            failure_reason=str(exc),
        )
        raise

    record_startup_report(
        supervisor,
        requested_agents=agent_names,
        restore=restore,
        auto_permission=auto_permission,
        status='ok',
        actions_taken=summary.actions_taken,
        cleanup_summaries=summary.cleanup_summaries,
        agent_results=summary.agent_results,
        failure_reason=None,
    )
    return summary


def _start_flow_kwargs(
    supervisor,
    *,
    agent_names: tuple[str, ...],
    target_agent_names: tuple[str, ...] | None,
    skipped_agent_results: tuple[CcbdStartupAgentResult, ...],
    restore: bool,
    auto_permission: bool,
    cleanup_tmux_orphans: bool,
    interactive_tmux_layout: bool,
) -> dict[str, object]:
    return {
        'project_root': supervisor._project_root,
        'project_id': supervisor._project_id,
        'paths': supervisor._paths,
        'config': supervisor._config,
        'runtime_service': supervisor._runtime_service,
        'requested_agents': agent_names,
        'target_agent_names': target_agent_names,
        'skipped_agent_results': skipped_agent_results,
        'restore': restore,
        'auto_permission': auto_permission,
        'cleanup_tmux_orphans': cleanup_tmux_orphans,
        'interactive_tmux_layout': interactive_tmux_layout,
        'clock': supervisor._clock,
    }


def start_targets(
    supervisor,
    agent_names: tuple[str, ...],
    *,
    skip_auto_start_blocked: bool = False,
) -> tuple[tuple[str, ...] | None, tuple[CcbdStartupAgentResult, ...]]:
    if not skip_auto_start_blocked:
        return None, ()
    target_agent_names = (
        tuple(agent_names)
        if agent_names
        else build_project_layout_plan(supervisor._config, requested_agents=agent_names).target_agent_names
    )
    filtered = tuple(
        agent_name
        for agent_name in target_agent_names
        if not runtime_auto_start_blocked(supervisor._registry.get(agent_name))
    )
    if len(filtered) == len(target_agent_names):
        return None, ()
    skipped = tuple(agent_name for agent_name in target_agent_names if agent_name not in set(filtered))
    return filtered, tuple(skipped_start_result(supervisor, agent_name) for agent_name in skipped)


def skipped_start_result(supervisor, agent_name: str) -> CcbdStartupAgentResult:
    runtime = supervisor._registry.get(agent_name)
    spec = supervisor._config.agents[agent_name]
    return CcbdStartupAgentResult(
        agent_name=agent_name,
        provider=getattr(spec, 'provider', None),
        action='skipped',
        health=str(getattr(runtime, 'health', None) or 'start-failed'),
        workspace_path=str(getattr(runtime, 'workspace_path', None) or ''),
        runtime_ref=getattr(runtime, 'runtime_ref', None),
        session_ref=getattr(runtime, 'session_ref', None),
        lifecycle_state=getattr(runtime, 'lifecycle_state', None),
        desired_state=getattr(runtime, 'desired_state', None),
        reconcile_state=getattr(runtime, 'reconcile_state', None),
        binding_source=str(getattr(getattr(runtime, 'binding_source', None), 'value', '') or '') or None,
        terminal_backend=getattr(runtime, 'terminal_backend', None),
        tmux_socket_name=getattr(runtime, 'tmux_socket_name', None),
        tmux_socket_path=getattr(runtime, 'tmux_socket_path', None),
        pane_id=getattr(runtime, 'pane_id', None),
        active_pane_id=getattr(runtime, 'active_pane_id', None),
        pane_state=getattr(runtime, 'pane_state', None),
        runtime_pid=getattr(runtime, 'runtime_pid', None),
        runtime_root=getattr(runtime, 'runtime_root', None),
        failure_reason=UNBOUND_MOUNT_FAILURE_REASON,
    )


def stop_all_supervisor(
    supervisor,
    *,
    force: bool,
    cleanup_project_tmux_orphans_by_socket_fn,
    tmux_cleanup_history_store_cls,
    stop_all_project_fn,
):
    try:
        execution = stop_all_project_fn(
            project_root=supervisor._project_root,
            project_id=supervisor._project_id,
            paths=supervisor._paths,
            registry=supervisor._registry,
            project_namespace=supervisor._project_namespace,
            clock=supervisor._clock,
            force=force,
            cleanup_project_tmux_orphans_by_socket_fn=cleanup_project_tmux_orphans_by_socket_fn,
            tmux_cleanup_history_store_cls=tmux_cleanup_history_store_cls,
        )
    except Exception as exc:
        record_shutdown_report(
            supervisor,
            trigger='stop_all',
            status='failed',
            forced=force,
            reason='stop_all',
            stopped_agents=(),
            actions_taken=('stop_all_failed',),
            cleanup_summaries=(),
            failure_reason=str(exc),
        )
        raise

    record_shutdown_report(
        supervisor,
        trigger='stop_all',
        status='ok',
        forced=force,
        reason='stop_all',
        stopped_agents=execution.stopped_agents,
        actions_taken=execution.actions_taken,
        cleanup_summaries=execution.cleanup_summaries,
        failure_reason=None,
    )
    try:
        supervisor._start_policy_store.clear()
    except Exception:
        pass
    return execution.summary


__all__ = ['start_supervisor', 'stop_all_supervisor']
