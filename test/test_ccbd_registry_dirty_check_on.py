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
    pid: int = 1234,
) -> AgentRuntime:
    return AgentRuntime(
        agent_name=name,
        state=state,
        pid=pid,
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


def test_flag_on_first_upsert_saves(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv('CCB_CCBD_DIRTY_CHECK', '1')
    registry, store = _registry(tmp_path)
    registry.upsert(_runtime())
    assert len(store.save_calls) == 1, 'first upsert must always persist (cache empty)'


def test_flag_on_heartbeat_only_changes_skip_disk(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv('CCB_CCBD_DIRTY_CHECK', '1')
    registry, store = _registry(tmp_path)
    base = _runtime()
    registry.upsert(base)
    for i in range(1, 5):
        registry.upsert(replace(base, last_seen_at=f'2026-04-30T00:00:0{i}Z'))

    # Only the initial save; subsequent heartbeat-only writes hit cache, skip disk.
    assert len(store.save_calls) == 1, f'expected 1 save (initial only); got {len(store.save_calls)}'


def test_flag_on_meaningful_change_persists(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv('CCB_CCBD_DIRTY_CHECK', '1')
    registry, store = _registry(tmp_path)
    base = _runtime()
    registry.upsert(base)
    # State change is meaningful.
    registry.upsert(replace(base, state=AgentState.BUSY, last_seen_at='2026-04-30T00:00:01Z'))
    # PID change is meaningful.
    registry.upsert(replace(base, state=AgentState.BUSY, pid=4321, last_seen_at='2026-04-30T00:00:02Z'))

    assert len(store.save_calls) == 3, 'state and pid changes must persist immediately'


def test_flag_on_cache_always_updated_for_heartbeat_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv('CCB_CCBD_DIRTY_CHECK', '1')
    registry, store = _registry(tmp_path)
    base = _runtime()
    registry.upsert(base)
    new_ts = '2026-04-30T00:01:00Z'
    registry.upsert(replace(base, last_seen_at=new_ts))

    cached = registry.get('agent1')
    assert cached is not None
    assert cached.last_seen_at == new_ts, 'cache must reflect latest heartbeat even when disk is skipped'


def test_unset_env_defaults_to_dirty_check_enabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Production default is enabled; explicit '0' is the emergency rollback path."""
    monkeypatch.delenv('CCB_CCBD_DIRTY_CHECK', raising=False)
    registry, store = _registry(tmp_path)
    base = _runtime()
    registry.upsert(base)
    registry.upsert(replace(base, last_seen_at='2026-04-30T00:00:01Z'))
    registry.upsert(replace(base, last_seen_at='2026-04-30T00:00:02Z'))

    assert len(store.save_calls) == 1, 'unset env must keep production dirty-check behavior enabled'
