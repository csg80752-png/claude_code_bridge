"""Periodic forced-flush invariants.

Each patched module flushes heartbeat-only changes within its configured interval:
  - registry.py: 60s
  - keeper_runtime/stores.py: 5s
  - services/mount.py: 5s
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

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
from ccbd.keeper_runtime.records import KeeperState
from ccbd.keeper_runtime.stores import KeeperStateStore
from ccbd.models import CcbdLease, MountState
from ccbd.services.mount import MountManager
from ccbd.services.registry import AgentRegistry
from storage.json_store import JsonStore
from storage.paths import PathLayout


class _RecordingRuntimeStore:
    def __init__(self) -> None:
        self.save_calls: list[AgentRuntime] = []

    def save(self, runtime: AgentRuntime) -> Path:
        self.save_calls.append(runtime)
        return Path('/tmp/recorded')

    def load(self, agent_name: str) -> AgentRuntime | None:
        return None

    def load_best_effort(self, agent_name: str) -> AgentRuntime | None:
        return None


class _CountingJsonStore:
    def __init__(self) -> None:
        self._inner = JsonStore()
        self.save_calls: list[Path] = []

    def save(self, path: Path, value: Any, serializer: Callable[[Any], dict] | None = None) -> None:
        self.save_calls.append(path)
        self._inner.save(path, value, serializer=serializer)

    def load(self, path: Path, *, loader=None):
        return self._inner.load(path, loader=loader)


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


def _config() -> ProjectConfig:
    return ProjectConfig(version=2, default_agents=('agent1',), agents={'agent1': _spec()})


def _runtime(last_seen_at: str = '2026-04-30T00:00:00Z') -> AgentRuntime:
    return AgentRuntime(
        agent_name='agent1',
        state=AgentState.IDLE,
        pid=1,
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


def _keeper_state(last_check_at: str = '2026-04-30T00:00:00Z') -> KeeperState:
    return KeeperState(
        project_id='proj-1',
        keeper_pid=555,
        started_at='2026-04-30T00:00:00Z',
        last_check_at=last_check_at,
        state='running',
        restart_count=0,
        last_restart_at='2026-04-30T00:00:00Z',
    )


def _install_clock(monkeypatch: pytest.MonkeyPatch, module_name: str, values: list[float]) -> None:
    import importlib
    mod = importlib.import_module(module_name)
    iterator = iter(values)
    fake = type('FakeTime', (), {'monotonic': staticmethod(lambda: next(iterator))})
    monkeypatch.setattr(mod, 'time', fake, raising=False)


def test_registry_forced_flush_after_60s(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Registry heartbeat-only writes flush at the 60s mark."""
    monkeypatch.setenv('CCB_CCBD_DIRTY_CHECK', '1')
    # initial: 1 call (set flush). suppressed@30: 1 call (check). flushed@61: 2 calls (check + set).
    _install_clock(monkeypatch, 'ccbd.services.registry', [0.0, 30.0, 61.0, 61.0])

    store = _RecordingRuntimeStore()
    registry = AgentRegistry(PathLayout(tmp_path), _config(), runtime_store=store)
    base = _runtime()
    registry.upsert(base)
    registry.upsert(replace(base, last_seen_at='2026-04-30T00:00:30Z'))  # suppressed
    registry.upsert(replace(base, last_seen_at='2026-04-30T00:01:01Z'))  # forced flush

    assert len(store.save_calls) == 2


def test_keeper_forced_flush_after_5s(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv('CCB_CCBD_DIRTY_CHECK', '1')
    _install_clock(monkeypatch, 'ccbd.keeper_runtime.stores', [0.0, 3.0, 6.0, 6.0])

    inner = _CountingJsonStore()
    store = KeeperStateStore(PathLayout(tmp_path), store=inner)
    store.save(_keeper_state())
    store.save(_keeper_state(last_check_at='2026-04-30T00:00:03Z'))  # suppressed
    store.save(_keeper_state(last_check_at='2026-04-30T00:00:06Z'))  # flush

    assert len(inner.save_calls) == 2


def test_mount_forced_flush_after_5s(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv('CCB_CCBD_DIRTY_CHECK', '1')
    _install_clock(monkeypatch, 'ccbd.services.mount', [0.0, 4.0, 6.0, 6.0])

    inner = _CountingJsonStore()
    mount_clock = iter(['2026-04-30T00:00:00Z', '2026-04-30T00:00:04Z', '2026-04-30T00:00:06Z'])
    mgr = MountManager(
        PathLayout(tmp_path),
        store=inner,
        clock=lambda: next(mount_clock),
        uid_getter=lambda: 1000,
        boot_id_getter=lambda: 'boot-id',
    )
    mgr.mark_mounted(project_id='proj-1', pid=1234, socket_path='/tmp/sock', generation=1)
    mgr.refresh_heartbeat()  # t=4 monotonic — suppressed
    mgr.refresh_heartbeat()  # t=6 monotonic — flush

    assert len(inner.save_calls) == 2


def test_registry_per_agent_flush_independent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Each agent has an independent flush clock — agent2's flush doesn't piggyback agent1's."""
    monkeypatch.setenv('CCB_CCBD_DIRTY_CHECK', '1')
    # 6 saves: 2 initial (1 call each) + 2 suppressed (1 call each) + 2 flushed (2 calls each) = 8 calls.
    _install_clock(
        monkeypatch,
        'ccbd.services.registry',
        [0.0, 0.1, 30.0, 30.1, 61.0, 61.0, 61.1, 61.1],
    )

    store = _RecordingRuntimeStore()
    config = ProjectConfig(
        version=2,
        default_agents=('agent1', 'agent2'),
        agents={'agent1': _spec('agent1'), 'agent2': _spec('agent2')},
    )
    registry = AgentRegistry(PathLayout(tmp_path), config, runtime_store=store)
    a1 = _runtime()
    a2 = AgentRuntime(
        agent_name='agent2',
        state=AgentState.IDLE,
        pid=2,
        started_at='2026-04-30T00:00:00Z',
        last_seen_at='2026-04-30T00:00:00Z',
        runtime_ref='tmux:%2',
        session_ref='session-2',
        workspace_path='/tmp/ws',
        project_id='proj-1',
        backend_type='tmux',
        queue_depth=0,
        socket_path=None,
        health='healthy',
    )
    registry.upsert(a1)  # initial
    registry.upsert(a2)  # initial
    registry.upsert(replace(a1, last_seen_at='2026-04-30T00:00:30Z'))  # t=30, suppressed
    registry.upsert(replace(a2, last_seen_at='2026-04-30T00:00:30Z'))  # t=30, suppressed
    registry.upsert(replace(a1, last_seen_at='2026-04-30T00:01:01Z'))  # t=61, forced flush
    registry.upsert(replace(a2, last_seen_at='2026-04-30T00:01:01Z'))  # t=61, forced flush

    # 2 initial + 2 forced = 4 saves
    assert len(store.save_calls) == 4
