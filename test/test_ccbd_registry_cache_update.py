from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from agents.models import (
    AgentRuntime,
    AgentSpec,
    AgentState,
    PermissionMode,
    ProjectConfig,
    QueuePolicy,
    RestoreMode,
    RuntimeMode,
    WorkspaceMode,
)
from ccbd.services.registry import AgentRegistry
from storage.paths import PathLayout


class _RecordingStore:
    def __init__(self) -> None:
        self.save_calls: list[AgentRuntime] = []

    def save(self, runtime: AgentRuntime) -> Path:
        self.save_calls.append(runtime)
        return Path('/tmp/recorded')

    def load(self, agent_name: str) -> AgentRuntime | None:
        return None

    def load_best_effort(self, agent_name: str) -> AgentRuntime | None:
        return None


def _spec(name: str = 'agent1') -> AgentSpec:
    return AgentSpec(
        name=name,
        provider='codex',
        target='.',
        workspace_mode=WorkspaceMode.GIT_WORKTREE,
        workspace_root=None,
        runtime_mode=RuntimeMode.PANE_BACKED,
        restore_default=RestoreMode.AUTO,
        permission_default=PermissionMode.MANUAL,
        queue_policy=QueuePolicy.SERIAL_PER_AGENT,
    )


def _config(name: str = 'agent1') -> ProjectConfig:
    return ProjectConfig(version=2, default_agents=(name,), agents={name: _spec(name)})


def _runtime(
    name: str = 'agent1',
    last_seen_at: str = '2026-04-30T00:00:00Z',
) -> AgentRuntime:
    return AgentRuntime(
        agent_name=name,
        state=AgentState.IDLE,
        pid=1234,
        started_at='2026-04-30T00:00:00Z',
        last_seen_at=last_seen_at,
        runtime_ref='tmux:%1',
        session_ref='session-1',
        workspace_path='/tmp/ws',
        project_id='proj-1',
        backend_type='tmux',
        queue_depth=0,
        socket_path=None,
        health='healthy',
    )


def _registry(tmp_path: Path) -> tuple[AgentRegistry, _RecordingStore]:
    layout = PathLayout(tmp_path)
    store = _RecordingStore()
    return AgentRegistry(layout, _config(), runtime_store=store), store


def test_update_cache_only_method_exists(tmp_path: Path) -> None:
    registry, _ = _registry(tmp_path)
    assert hasattr(registry, 'update_cache_only'), 'patch 1 must add update_cache_only(runtime) method'
    assert callable(getattr(registry, 'update_cache_only'))


def test_update_cache_only_updates_cache(tmp_path: Path) -> None:
    registry, store = _registry(tmp_path)
    runtime = _runtime()
    result = registry.update_cache_only(runtime)
    assert result is runtime
    assert registry.get('agent1') is runtime


def test_update_cache_only_does_not_save_to_disk(tmp_path: Path) -> None:
    registry, store = _registry(tmp_path)
    registry.update_cache_only(_runtime())
    assert store.save_calls == [], 'update_cache_only must not call runtime_store.save'


def test_update_cache_only_validates_agent_name(tmp_path: Path) -> None:
    """spec_for() validation runs before cache update — unknown agent must raise."""
    registry, _ = _registry(tmp_path)
    bogus = _runtime(name='unknown-agent')
    # AgentRegistry constructor normalizes; an unknown agent should still fail spec_for lookup.
    # Use an agent name that isn't registered in _config().
    other = AgentRuntime(
        agent_name='nonexistent',
        state=AgentState.IDLE,
        pid=42,
        started_at='2026-04-30T00:00:00Z',
        last_seen_at='2026-04-30T00:00:00Z',
        runtime_ref='tmux:%1',
        session_ref='session-z',
        workspace_path='/tmp/ws',
        project_id='proj-1',
        backend_type='tmux',
        queue_depth=0,
        socket_path=None,
        health='healthy',
    )
    with pytest.raises(KeyError):
        registry.update_cache_only(other)


def test_update_cache_only_repeated_calls_keep_latest(tmp_path: Path) -> None:
    registry, store = _registry(tmp_path)
    base = _runtime()
    registry.update_cache_only(base)
    registry.update_cache_only(replace(base, last_seen_at='2026-04-30T00:00:05Z'))
    registry.update_cache_only(replace(base, last_seen_at='2026-04-30T00:00:10Z'))
    cached = registry.get('agent1')
    assert cached is not None
    assert cached.last_seen_at == '2026-04-30T00:00:10Z'
    assert store.save_calls == []
