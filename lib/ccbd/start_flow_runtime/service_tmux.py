from __future__ import annotations

from cli.services.tmux_start_layout import TmuxStartLayout

from .binding import bootstrap_project_namespace_cmd_pane
from .layout import cleanup_start_tmux_orphans, prepare_start_layout, session_root_pane


def tmux_namespace_runtime(
    deps,
    *,
    tmux_socket_path: str | None,
    tmux_session_name: str | None,
    tmux_workspace_window_name: str | None,
):
    tmux_backend = deps.tmux_backend_cls(socket_path=tmux_socket_path) if tmux_socket_path is not None else None
    if tmux_backend is None or not tmux_session_name:
        return tmux_backend, None
    return tmux_backend, session_root_pane(
        deps,
        tmux_backend,
        tmux_session_name,
        workspace_window_name=tmux_workspace_window_name,
    )


def tmux_layout_for_start(
    deps,
    context,
    *,
    config,
    prepared_agents,
    interactive_tmux_layout: bool,
    tmux_backend,
    root_pane_id: str | None,
    actions_taken: list[str],
) -> TmuxStartLayout:
    if not interactive_tmux_layout:
        return TmuxStartLayout(
            cmd_pane_id=None,
            agent_panes=existing_project_slot_panes(
                tmux_backend,
                project_id=context.project.project_id,
                prepared_agents=prepared_agents,
            ),
        )
    deps.set_tmux_ui_active_fn(True)
    launch_targets = tuple(item.agent_name for item in prepared_agents if item.binding is None)
    if launch_targets:
        actions_taken.append(f'prepare_tmux_layout:{",".join(launch_targets)}')
    return prepare_start_layout(
        deps,
        context,
        config=config,
        targets=launch_targets,
        layout_plan=(
            deps.build_project_layout_plan_fn(config, target_agent_names=launch_targets)
            if launch_targets
            else None
        ),
        tmux_backend=tmux_backend,
        root_pane_id=root_pane_id,
    )


def existing_project_slot_panes(tmux_backend, *, project_id: str, prepared_agents) -> dict[str, str]:
    if tmux_backend is None:
        return {}
    panes: dict[str, str] = {}
    for item in prepared_agents:
        if item.binding is not None:
            continue
        agent_name = str(item.agent_name or '').strip()
        if not agent_name:
            continue
        pane_id = existing_project_slot_pane(
            tmux_backend,
            project_id=project_id,
            agent_name=agent_name,
        )
        if pane_id is not None:
            panes[agent_name] = pane_id
    return panes


def existing_project_slot_pane(tmux_backend, *, project_id: str, agent_name: str) -> str | None:
    list_fn = getattr(tmux_backend, 'list_panes_by_user_options', None)
    if not callable(list_fn):
        return None
    try:
        matches = list_fn({'@ccb_project_id': project_id, '@ccb_slot': agent_name})
    except Exception:
        return None
    live_matches: list[str] = []
    for pane_id in matches or ():
        pane_text = str(pane_id or '').strip()
        if not pane_text.startswith('%'):
            continue
        is_alive = getattr(tmux_backend, 'is_pane_alive', None)
        if callable(is_alive):
            try:
                if not is_alive(pane_text):
                    continue
            except Exception:
                continue
        live_matches.append(pane_text)
    if len(live_matches) != 1:
        return None
    return live_matches[0]


def project_socket_active_panes(
    *,
    tmux_layout: TmuxStartLayout,
    tmux_socket_path: str | None,
    config,
    root_pane_id: str | None,
) -> tuple[list[str], str | None]:
    active_panes: list[str] = []
    if root_pane_id and tmux_socket_path is not None:
        active_panes.append(root_pane_id)
    cmd_pane_id = tmux_layout.cmd_pane_id
    if cmd_pane_id is None and tmux_socket_path is not None and bool(getattr(config, 'cmd_enabled', False)):
        cmd_pane_id = root_pane_id
    if cmd_pane_id and tmux_socket_path is not None and cmd_pane_id not in active_panes:
        active_panes.append(cmd_pane_id)
    return active_panes, cmd_pane_id


def bootstrap_cmd_pane_if_needed(
    deps,
    *,
    fresh_namespace: bool,
    cmd_pane_id: str | None,
    project_root,
    project_id: str,
    tmux_socket_path: str | None,
    namespace_epoch: int | None,
    actions_taken: list[str],
) -> None:
    if not fresh_namespace or cmd_pane_id is None:
        return
    bootstrapped_cmd_pane = bootstrap_project_namespace_cmd_pane(
        deps,
        pane_id=cmd_pane_id,
        project_root=project_root,
        project_id=project_id,
        tmux_socket_path=tmux_socket_path,
        namespace_epoch=namespace_epoch,
    )
    if bootstrapped_cmd_pane is not None:
        actions_taken.append(f'bootstrap_cmd_pane:{bootstrapped_cmd_pane}')


def record_active_panes(
    active_panes_by_socket: dict[str | None, list[str]],
    project_socket_active_panes: list[str],
    *,
    execution,
) -> None:
    if execution.runtime_pane_id is not None:
        active_panes_by_socket.setdefault(execution.socket_name, []).append(execution.runtime_pane_id)
    if execution.project_socket_active_pane_id is not None:
        project_socket_active_panes.append(execution.project_socket_active_pane_id)


def cleanup_tmux_orphans_if_needed(
    deps,
    *,
    cleanup_tmux_orphans: bool,
    project_id: str,
    paths,
    active_panes_by_socket: dict[str | None, list[str]],
    project_socket_active_panes: list[str],
    tmux_socket_path: str | None,
    clock,
    actions_taken: list[str],
) -> tuple[object, ...]:
    if not cleanup_tmux_orphans:
        return ()
    cleanup_summaries = cleanup_start_tmux_orphans(
        deps,
        project_id=project_id,
        paths=paths,
        active_panes_by_socket=active_panes_by_socket,
        project_socket_active_panes=project_socket_active_panes,
        tmux_socket_path=tmux_socket_path,
        clock=clock,
    )
    total_killed = sum(len(item.killed_panes) for item in cleanup_summaries)
    actions_taken.append(f'cleanup_tmux_orphans:killed={total_killed}')
    return tuple(cleanup_summaries)


__all__ = [
    'bootstrap_cmd_pane_if_needed',
    'cleanup_tmux_orphans_if_needed',
    'existing_project_slot_pane',
    'existing_project_slot_panes',
    'project_socket_active_panes',
    'record_active_panes',
    'tmux_layout_for_start',
    'tmux_namespace_runtime',
]
