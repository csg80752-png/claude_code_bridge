from __future__ import annotations

from types import SimpleNamespace

from ccbd.start_runtime.agent_runtime import start_agent_runtime
from cli.services.provider_binding import AgentBinding
from cli.services.runtime_launch import RuntimeLaunchResult


class _RuntimeService:
    def __init__(self) -> None:
        self.attach_calls: list[dict[str, object]] = []
        self.restore_calls: list[str] = []

    def attach(self, **kwargs):
        self.attach_calls.append(kwargs)
        binding_source = SimpleNamespace(value=kwargs['binding_source'])
        return SimpleNamespace(
            runtime_ref=kwargs['runtime_ref'],
            session_ref=kwargs['session_ref'],
            lifecycle_state=kwargs['lifecycle_state'],
            desired_state=None,
            reconcile_state=None,
            binding_source=binding_source,
            terminal_backend=kwargs['terminal_backend'],
            tmux_socket_name=kwargs['tmux_socket_name'],
            tmux_socket_path=kwargs['tmux_socket_path'],
            pane_id=kwargs['pane_id'],
            active_pane_id=kwargs['active_pane_id'],
            pane_state=kwargs['pane_state'],
            runtime_pid=kwargs['runtime_pid'],
            runtime_root=kwargs['runtime_root'],
            last_failure_reason=kwargs['last_failure_reason'],
        )

    def restore(self, agent_name: str):
        self.restore_calls.append(agent_name)


def _binding(**overrides) -> AgentBinding:
    values = {
        'runtime_ref': 'tmux:%5',
        'session_ref': 'session-5',
        'provider': 'codex',
        'runtime_root': '/tmp/runtime',
        'runtime_pid': 55,
        'session_file': '/tmp/session.json',
        'session_id': 'session-5',
        'tmux_socket_name': 'sock-a',
        'tmux_socket_path': '/tmp/ccb.sock',
        'terminal': 'tmux',
        'pane_id': '%5',
        'active_pane_id': '%5',
        'pane_title_marker': 'agent1',
        'pane_state': 'alive',
    }
    values.update(overrides)
    return AgentBinding(**values)


def test_start_agent_runtime_degrades_unresolved_stale_binding() -> None:
    runtime_service = _RuntimeService()

    execution = start_agent_runtime(
        context=object(),
        command=SimpleNamespace(restore=False),
        runtime_service=runtime_service,
        agent_name='agent1',
        spec=SimpleNamespace(provider='codex', runtime_mode=SimpleNamespace(value='pane-backed')),
        plan=SimpleNamespace(workspace_path='/tmp/ws'),
        binding=None,
        raw_binding=None,
        stale_binding=True,
        assigned_pane_id='%9',
        style_index=0,
        project_id='proj-1',
        tmux_socket_path='/tmp/ccb.sock',
        namespace_epoch=2,
        ensure_agent_runtime_fn=lambda *args, **kwargs: RuntimeLaunchResult(launched=False, binding=None),
        launch_binding_hint_fn=lambda **kwargs: None,
        relabel_project_namespace_pane_fn=lambda **kwargs: None,
        same_tmux_socket_path_fn=lambda left, right: left == right,
    )

    assert execution.agent_result.action == 'degraded'
    assert execution.agent_result.health == 'degraded'
    assert execution.agent_result.failure_reason == 'stale_binding_unresolved'
    assert execution.actions_taken == ('degraded_stale_binding:agent1',)
    assert runtime_service.restore_calls == []


def test_start_agent_runtime_degrades_missing_binding_after_launch() -> None:
    runtime_service = _RuntimeService()

    execution = start_agent_runtime(
        context=object(),
        command=SimpleNamespace(restore=False),
        runtime_service=runtime_service,
        agent_name='agent1',
        spec=SimpleNamespace(provider='codex', runtime_mode=SimpleNamespace(value='pane-backed')),
        plan=SimpleNamespace(workspace_path='/tmp/ws'),
        binding=None,
        raw_binding=None,
        stale_binding=False,
        assigned_pane_id='%9',
        style_index=0,
        project_id='proj-1',
        tmux_socket_path='/tmp/ccb.sock',
        namespace_epoch=2,
        ensure_agent_runtime_fn=lambda *args, **kwargs: RuntimeLaunchResult(launched=False, binding=None),
        launch_binding_hint_fn=lambda **kwargs: None,
        relabel_project_namespace_pane_fn=lambda **kwargs: None,
        same_tmux_socket_path_fn=lambda left, right: left == right,
    )

    assert execution.agent_result.action == 'degraded'
    assert execution.agent_result.health == 'degraded'
    assert execution.agent_result.failure_reason == 'binding_missing_after_launch'
    assert execution.actions_taken == ('degraded_missing_binding:agent1',)
    assert runtime_service.attach_calls[-1]['runtime_ref'] == ''
    assert runtime_service.attach_calls[-1]['session_ref'] == ''
    assert runtime_service.attach_calls[-1]['last_failure_reason'] == 'binding_missing_after_launch'
    assert runtime_service.attach_calls[-1]['clear_failure_reason'] is False


def test_start_agent_runtime_persists_launch_exception_as_degraded_failure() -> None:
    runtime_service = _RuntimeService()

    execution = start_agent_runtime(
        context=object(),
        command=SimpleNamespace(restore=False),
        runtime_service=runtime_service,
        agent_name='agent3',
        spec=SimpleNamespace(provider='claude', runtime_mode=SimpleNamespace(value='pane-backed')),
        plan=SimpleNamespace(workspace_path='/tmp/ws'),
        binding=None,
        raw_binding=None,
        stale_binding=True,
        assigned_pane_id='%4',
        style_index=2,
        project_id='proj-1',
        tmux_socket_path='/tmp/ccb.sock',
        namespace_epoch=2,
        ensure_agent_runtime_fn=lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError('failed to resolve usable binding for agent3 after claude launch')
        ),
        launch_binding_hint_fn=lambda **kwargs: None,
        relabel_project_namespace_pane_fn=lambda **kwargs: None,
        same_tmux_socket_path_fn=lambda left, right: left == right,
    )

    assert execution.agent_result.action == 'degraded'
    assert execution.agent_result.health == 'degraded'
    assert execution.agent_result.failure_reason == (
        'launch_binding_failed: RuntimeError: failed to resolve usable binding for agent3 after claude launch'
    )
    assert execution.actions_taken == (
        'degraded_launch_error:agent3:RuntimeError: failed to resolve usable binding for agent3 after claude launch',
    )
    assert runtime_service.attach_calls[-1]['last_failure_reason'] == execution.agent_result.failure_reason
    assert runtime_service.attach_calls[-1]['clear_failure_reason'] is False


def test_start_agent_runtime_does_not_mask_post_launch_relabel_exception() -> None:
    runtime_service = _RuntimeService()
    launched_binding = _binding(runtime_ref='tmux:%7', session_ref='session-7', pane_id='%7', active_pane_id='%7')

    try:
        start_agent_runtime(
            context=object(),
            command=SimpleNamespace(restore=False),
            runtime_service=runtime_service,
            agent_name='agent1',
            spec=SimpleNamespace(provider='codex', runtime_mode=SimpleNamespace(value='pane-backed')),
            plan=SimpleNamespace(workspace_path='/tmp/ws'),
            binding=None,
            raw_binding=_binding(runtime_ref='tmux:%3'),
            stale_binding=True,
            assigned_pane_id='%7',
            style_index=2,
            project_id='proj-1',
            tmux_socket_path='/tmp/ccb.sock',
            namespace_epoch=4,
            ensure_agent_runtime_fn=lambda *args, **kwargs: RuntimeLaunchResult(launched=True, binding=launched_binding),
            launch_binding_hint_fn=lambda **kwargs: 'hint',
            relabel_project_namespace_pane_fn=lambda **kwargs: (_ for _ in ()).throw(RuntimeError('relabel boom')),
            same_tmux_socket_path_fn=lambda left, right: left == right,
        )
    except RuntimeError as exc:
        assert str(exc) == 'relabel boom'
    else:
        raise AssertionError('expected relabel exception to propagate')

    assert runtime_service.attach_calls == []


def test_start_agent_runtime_does_not_mask_launch_hint_exception() -> None:
    runtime_service = _RuntimeService()

    try:
        start_agent_runtime(
            context=object(),
            command=SimpleNamespace(restore=False),
            runtime_service=runtime_service,
            agent_name='agent1',
            spec=SimpleNamespace(provider='codex', runtime_mode=SimpleNamespace(value='pane-backed')),
            plan=SimpleNamespace(workspace_path='/tmp/ws'),
            binding=None,
            raw_binding=_binding(runtime_ref='tmux:%3'),
            stale_binding=True,
            assigned_pane_id='%7',
            style_index=2,
            project_id='proj-1',
            tmux_socket_path='/tmp/ccb.sock',
            namespace_epoch=4,
            ensure_agent_runtime_fn=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('should not launch')),
            launch_binding_hint_fn=lambda **kwargs: (_ for _ in ()).throw(RuntimeError('hint boom')),
            relabel_project_namespace_pane_fn=lambda **kwargs: '%7',
            same_tmux_socket_path_fn=lambda left, right: left == right,
        )
    except RuntimeError as exc:
        assert str(exc) == 'hint boom'
    else:
        raise AssertionError('expected launch hint exception to propagate')

    assert runtime_service.attach_calls == []


def test_start_agent_runtime_allows_headless_without_binding() -> None:
    runtime_service = _RuntimeService()

    execution = start_agent_runtime(
        context=object(),
        command=SimpleNamespace(restore=False),
        runtime_service=runtime_service,
        agent_name='agent1',
        spec=SimpleNamespace(provider='codex', runtime_mode=SimpleNamespace(value='headless')),
        plan=SimpleNamespace(workspace_path='/tmp/ws'),
        binding=None,
        raw_binding=None,
        stale_binding=False,
        assigned_pane_id=None,
        style_index=0,
        project_id='proj-1',
        tmux_socket_path=None,
        namespace_epoch=None,
        ensure_agent_runtime_fn=lambda *args, **kwargs: RuntimeLaunchResult(launched=False, binding=None),
        launch_binding_hint_fn=lambda **kwargs: None,
        relabel_project_namespace_pane_fn=lambda **kwargs: None,
        same_tmux_socket_path_fn=lambda left, right: left == right,
    )

    assert execution.agent_result.action == 'attached'
    assert execution.agent_result.health == 'healthy'
    assert execution.agent_result.failure_reason is None
    assert runtime_service.attach_calls[-1]['runtime_ref'] is None
    assert runtime_service.attach_calls[-1]['session_ref'] is None
    assert runtime_service.attach_calls[-1]['last_failure_reason'] is None
    assert runtime_service.attach_calls[-1]['clear_failure_reason'] is True


def test_start_agent_runtime_degrades_partial_binding_after_launch() -> None:
    runtime_service = _RuntimeService()
    partial_binding = _binding(runtime_ref=None, session_ref=None, pane_id='%9', active_pane_id='%9')

    execution = start_agent_runtime(
        context=object(),
        command=SimpleNamespace(restore=False),
        runtime_service=runtime_service,
        agent_name='agent1',
        spec=SimpleNamespace(provider='codex', runtime_mode=SimpleNamespace(value='pane-backed')),
        plan=SimpleNamespace(workspace_path='/tmp/ws'),
        binding=None,
        raw_binding=None,
        stale_binding=False,
        assigned_pane_id='%9',
        style_index=0,
        project_id='proj-1',
        tmux_socket_path='/tmp/ccb.sock',
        namespace_epoch=2,
        ensure_agent_runtime_fn=lambda *args, **kwargs: RuntimeLaunchResult(launched=True, binding=partial_binding),
        launch_binding_hint_fn=lambda **kwargs: None,
        relabel_project_namespace_pane_fn=lambda **kwargs: None,
        same_tmux_socket_path_fn=lambda left, right: left == right,
    )

    assert execution.agent_result.action == 'degraded'
    assert execution.agent_result.health == 'degraded'
    assert execution.agent_result.failure_reason == 'partial_binding_unresolved'
    assert execution.actions_taken == ('degraded_partial_binding:agent1',)
    assert runtime_service.attach_calls[-1]['runtime_ref'] == ''
    assert runtime_service.attach_calls[-1]['session_ref'] == ''


def test_start_agent_runtime_reuses_binding_and_restores_when_requested() -> None:
    runtime_service = _RuntimeService()
    binding = _binding()

    execution = start_agent_runtime(
        context=object(),
        command=SimpleNamespace(restore=True),
        runtime_service=runtime_service,
        agent_name='agent1',
        spec=SimpleNamespace(provider='codex', runtime_mode=SimpleNamespace(value='pane-backed')),
        plan=SimpleNamespace(workspace_path='/tmp/ws'),
        binding=binding,
        raw_binding=binding,
        stale_binding=False,
        assigned_pane_id=None,
        style_index=1,
        project_id='proj-1',
        tmux_socket_path='/tmp/ccb.sock',
        namespace_epoch=3,
        ensure_agent_runtime_fn=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('should not relaunch')),
        launch_binding_hint_fn=lambda **kwargs: None,
        relabel_project_namespace_pane_fn=lambda **kwargs: '%5',
        same_tmux_socket_path_fn=lambda left, right: left == right,
    )

    assert execution.agent_result.action == 'attached'
    assert execution.actions_taken == (
        'relabel_runtime_pane:agent1:%5',
        'reuse_binding:agent1',
        'restore_runtime:agent1',
    )
    assert execution.runtime_pane_id == '%5'
    assert execution.project_socket_active_pane_id == '%5'
    assert runtime_service.restore_calls == ['agent1']


def test_start_agent_runtime_relaunches_and_tracks_project_socket_pane() -> None:
    runtime_service = _RuntimeService()
    launched_binding = _binding(runtime_ref='tmux:%7', session_ref='session-7', pane_id='%7', active_pane_id='%7')

    execution = start_agent_runtime(
        context=object(),
        command=SimpleNamespace(restore=False),
        runtime_service=runtime_service,
        agent_name='agent1',
        spec=SimpleNamespace(provider='codex', runtime_mode=SimpleNamespace(value='pane-backed')),
        plan=SimpleNamespace(workspace_path='/tmp/ws'),
        binding=None,
        raw_binding=_binding(runtime_ref='tmux:%3'),
        stale_binding=True,
        assigned_pane_id='%7',
        style_index=2,
        project_id='proj-1',
        tmux_socket_path='/tmp/ccb.sock',
        namespace_epoch=4,
        ensure_agent_runtime_fn=lambda *args, **kwargs: RuntimeLaunchResult(launched=True, binding=launched_binding),
        launch_binding_hint_fn=lambda **kwargs: 'hint',
        relabel_project_namespace_pane_fn=lambda **kwargs: '%7',
        same_tmux_socket_path_fn=lambda left, right: left == right,
    )

    assert execution.agent_result.action == 'relaunched'
    assert execution.actions_taken == (
        'relabel_runtime_pane:agent1:%7',
        'relaunch_runtime:agent1',
    )
    assert execution.runtime_pane_id == '%7'
    assert execution.project_socket_active_pane_id == '%7'
    assert runtime_service.attach_calls[-1]['runtime_ref'] == 'tmux:%7'
