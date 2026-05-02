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
    state: AgentState = AgentState.IDLE,
) -> AgentRuntime:
    return AgentRuntime(
        agent_name=name,
        state=state,
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


def test_explicit_zero_saves_every_upsert(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv('CCB_CCBD_DIRTY_CHECK', '0')
    registry, store = _registry(tmp_path)
    assert hasattr(registry, 'update_cache_only'), 'AgentRegistry must expose update_cache_only after patch 1'

    base = _runtime()
    registry.upsert(base)
    for i in range(1, 5):
        registry.upsert(replace(base, last_seen_at=f'2026-04-30T00:00:0{i}Z'))

    assert len(store.save_calls) == 5


def test_explicit_zero_treated_as_rollback_opt_out(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv('CCB_CCBD_DIRTY_CHECK', '0')
    registry, store = _registry(tmp_path)
    assert hasattr(registry, 'update_cache_only')

    base = _runtime()
    registry.upsert(base)
    registry.upsert(replace(base, last_seen_at='2026-04-30T00:00:01Z'))
    registry.upsert(replace(base, last_seen_at='2026-04-30T00:00:02Z'))

    assert len(store.save_calls) == 3


def test_explicit_zero_save_happens_before_cache_update(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv('CCB_CCBD_DIRTY_CHECK', '0')
    registry, store = _registry(tmp_path)
    assert hasattr(registry, 'update_cache_only')

    order: list[str] = []
    real_save = store.save

    def tracked(runtime: AgentRuntime) -> Path:
        order.append('save')
        return real_save(runtime)

    store.save = tracked  # type: ignore[assignment]

    class CacheTracker(dict):
        def __setitem__(self, key, value):
            order.append('cache')
            super().__setitem__(key, value)

    registry._cache = CacheTracker()
    registry.upsert(_runtime())

    assert order == ['save', 'cache'], f'expected save-before-cache, got {order}'
