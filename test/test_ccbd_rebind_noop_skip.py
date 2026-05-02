from __future__ import annotations

from types import SimpleNamespace

import pytest

from agents.models import AgentRuntime, AgentState
from ccbd.services.health_monitor_runtime.updates_runtime.rebind import rebind_runtime
from ccbd.services.provider_runtime_facts import ProviderRuntimeFacts


def _runtime(**overrides) -> AgentRuntime:
    values = {
        'agent_name': 'agent1',
        'state': AgentState.IDLE,
        # pid must match facts.runtime_pid to make rebind a true no-op
        # (rebind sets runtime.pid <- facts.runtime_pid via _next_pid())
        'pid': 22,
        'started_at': '2026-04-30T00:00:00Z',
        'last_seen_at': '2026-04-30T00:00:01Z',
        'runtime_ref': 'tmux:%9',
        'session_ref': 'runtime-session',
        'workspace_path': '/tmp/workspace',
        'project_id': 'proj-1',
        'backend_type': 'pane-backed',
        'queue_depth': 0,
        'socket_path': None,
        'health': 'healthy',
        'provider': 'codex',
        'runtime_root': '/tmp/runtime',
        'runtime_pid': 22,
        'terminal_backend': 'tmux',
        'pane_id': '%9',
        'active_pane_id': '%9',
        'pane_title_marker': 'agent1',
        'pane_state': 'alive',
        'tmux_socket_name': 'sock',
        'tmux_socket_path': '/tmp/tmux.sock',
        'session_file': '/tmp/session.json',
        'session_id': 'sid-9',
    }
    values.update(overrides)
    return AgentRuntime(**values)


def _matching_facts(runtime: AgentRuntime) -> ProviderRuntimeFacts:
    return ProviderRuntimeFacts(
        runtime_ref=runtime.runtime_ref,
        session_ref=runtime.session_ref,
        runtime_root=runtime.runtime_root,
        runtime_pid=runtime.runtime_pid,
        terminal_backend=runtime.terminal_backend,
        pane_id=runtime.pane_id,
        pane_title_marker=runtime.pane_title_marker,
        pane_state=runtime.pane_state,
        tmux_socket_name=runtime.tmux_socket_name,
        tmux_socket_path=runtime.tmux_socket_path,
        session_file=runtime.session_file,
        session_id=runtime.session_id,
    )


class _RegistryProbe:
    def __init__(self) -> None:
        self.upsert_calls: list[AgentRuntime] = []
        self.cache_only_calls: list[AgentRuntime] = []

    def upsert(self, runtime: AgentRuntime) -> AgentRuntime:
        self.upsert_calls.append(runtime)
        return runtime

    def update_cache_only(self, runtime: AgentRuntime) -> AgentRuntime:
        self.cache_only_calls.append(runtime)
        return runtime


def _monitor(registry: _RegistryProbe, runtime: AgentRuntime, *, clock: str = '2026-04-30T00:00:05Z'):
    facts = _matching_facts(runtime)
    return SimpleNamespace(
        _provider_runtime_facts=lambda runtime, session, binding, pane_id_override=None: facts,
        _clock=lambda: clock,
        _registry=registry,
    )


def _binding() -> SimpleNamespace:
    return SimpleNamespace(session_id_attr='session_id', session_path_attr='session_path')


def _session() -> SimpleNamespace:
    return SimpleNamespace(pane_id='%9', session_id='sid-9', session_path='/tmp/session.json')


def test_flag_on_noop_rebind_skips_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('CCB_CCBD_DIRTY_CHECK', '1')
    runtime = _runtime()
    registry = _RegistryProbe()
    monitor = _monitor(registry, runtime)
    rebind_runtime(monitor, runtime, _session(), _binding())

    assert registry.upsert_calls == [], 'no-op rebind under flag-on must not upsert'
    assert len(registry.cache_only_calls) == 1, 'no-op rebind under flag-on must call update_cache_only'
    cached = registry.cache_only_calls[0]
    assert cached.last_seen_at == '2026-04-30T00:00:05Z', 'in-memory cache must reflect refreshed last_seen_at'


def test_flag_on_semantic_change_still_upserts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('CCB_CCBD_DIRTY_CHECK', '1')
    # runtime started DEGRADED → _next_state will return IDLE (semantic change)
    runtime = _runtime(state=AgentState.DEGRADED)
    registry = _RegistryProbe()
    monitor = _monitor(registry, runtime)
    rebind_runtime(monitor, runtime, _session(), _binding())

    assert len(registry.upsert_calls) == 1, 'state recovery from DEGRADED must upsert (disk persist)'
    assert registry.cache_only_calls == []


def test_flag_on_pid_change_still_upserts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('CCB_CCBD_DIRTY_CHECK', '1')
    runtime = _runtime()
    registry = _RegistryProbe()
    # facts return a different runtime_pid → semantic change
    facts = ProviderRuntimeFacts(
        runtime_ref=runtime.runtime_ref,
        session_ref=runtime.session_ref,
        runtime_root=runtime.runtime_root,
        runtime_pid=99999,
        terminal_backend=runtime.terminal_backend,
        pane_id=runtime.pane_id,
        pane_title_marker=runtime.pane_title_marker,
        pane_state=runtime.pane_state,
        tmux_socket_name=runtime.tmux_socket_name,
        tmux_socket_path=runtime.tmux_socket_path,
        session_file=runtime.session_file,
        session_id=runtime.session_id,
    )
    monitor = SimpleNamespace(
        _provider_runtime_facts=lambda *a, **kw: facts,
        _clock=lambda: '2026-04-30T00:00:05Z',
        _registry=registry,
    )
    rebind_runtime(monitor, runtime, _session(), _binding())

    assert len(registry.upsert_calls) == 1
    assert registry.cache_only_calls == []


def test_explicit_zero_noop_rebind_still_upserts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('CCB_CCBD_DIRTY_CHECK', '0')
    runtime = _runtime()
    registry = _RegistryProbe()
    monitor = _monitor(registry, runtime)
    rebind_runtime(monitor, runtime, _session(), _binding())

    assert len(registry.upsert_calls) == 1, 'explicit zero must preserve rollback always-upsert behavior'
    assert registry.cache_only_calls == []
