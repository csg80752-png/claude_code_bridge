"""Rollback smoke test: with `CCB_CCBD_DIRTY_CHECK=0`, patched modules are byte-identical
in observable behavior to pre-patch code.

Runs the patched code paths against the explicit rollback/off contract:
  - registry: every upsert hits disk.
  - rebind: always upserts.
  - keeper: every save hits disk.
  - mount: every refresh writes lease.json.

Includes hasattr() guards against `update_cache_only` to verify the new method exists
even when rollback/off (regression guard for emergency opt-out).
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
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
from ccbd.services.health_monitor_runtime.updates_runtime.rebind import rebind_runtime
from ccbd.services.mount import MountManager
from ccbd.services.provider_runtime_facts import ProviderRuntimeFacts
from ccbd.services.registry import AgentRegistry
from storage.json_store import JsonStore
from storage.paths import PathLayout


@pytest.fixture(autouse=True)
def _flag_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('CCB_CCBD_DIRTY_CHECK', '0')


class _RecordingRuntimeStore:
    def __init__(self) -> None:
        self.save_calls: list[AgentRuntime] = []

    def save(self, runtime: AgentRuntime) -> Path:
        self.save_calls.append(runtime)
        return Path('/tmp/r')

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


def test_explicit_zero_registry_saves_every_upsert(tmp_path: Path) -> None:
    store = _RecordingRuntimeStore()
    registry = AgentRegistry(PathLayout(tmp_path), _config(), runtime_store=store)
    assert hasattr(registry, 'update_cache_only'), 'patch must add update_cache_only attr regardless of flag'
    base = _runtime()
    for i in range(3):
        registry.upsert(replace(base, last_seen_at=f'2026-04-30T00:00:0{i}Z'))
    assert len(store.save_calls) == 3


def test_explicit_zero_keeper_saves_every_call(tmp_path: Path) -> None:
    inner = _CountingJsonStore()
    store = KeeperStateStore(PathLayout(tmp_path), store=inner)
    s = KeeperState(
        project_id='proj-1', keeper_pid=555, started_at='2026-04-30T00:00:00Z',
        last_check_at='2026-04-30T00:00:00Z', state='running', restart_count=0,
        last_restart_at='2026-04-30T00:00:00Z',
    )
    store.save(s)
    store.save(replace(s, last_check_at='2026-04-30T00:00:01Z'))
    store.save(replace(s, last_check_at='2026-04-30T00:00:02Z'))
    assert len(inner.save_calls) == 3


def test_explicit_zero_mount_writes_every_refresh(tmp_path: Path) -> None:
    inner = _CountingJsonStore()
    mgr = MountManager(
        PathLayout(tmp_path), store=inner,
        clock=lambda: '2026-04-30T00:00:00Z',
        uid_getter=lambda: 1000, boot_id_getter=lambda: 'b',
    )
    mgr.mark_mounted(project_id='proj-1', pid=1234, socket_path='/tmp/sock', generation=1)
    mgr.refresh_heartbeat()
    mgr.refresh_heartbeat()
    assert len(inner.save_calls) == 3


def test_explicit_zero_rebind_always_upserts() -> None:
    runtime = _runtime()
    upsert_calls: list[AgentRuntime] = []
    cache_calls: list[AgentRuntime] = []
    facts = ProviderRuntimeFacts(
        runtime_ref=runtime.runtime_ref, session_ref=runtime.session_ref,
        runtime_root=runtime.runtime_root, runtime_pid=runtime.runtime_pid,
        terminal_backend=runtime.terminal_backend, pane_id=runtime.pane_id,
        pane_title_marker=runtime.pane_title_marker, pane_state=runtime.pane_state,
        tmux_socket_name=runtime.tmux_socket_name, tmux_socket_path=runtime.tmux_socket_path,
        session_file=runtime.session_file, session_id=runtime.session_id,
    )
    monitor = SimpleNamespace(
        _provider_runtime_facts=lambda *a, **kw: facts,
        _clock=lambda: '2026-04-30T00:00:05Z',
        _registry=SimpleNamespace(
            upsert=lambda r: (upsert_calls.append(r), r)[1],
            update_cache_only=lambda r: (cache_calls.append(r), r)[1],
        ),
    )
    binding = SimpleNamespace(session_id_attr='session_id', session_path_attr='session_path')
    session = SimpleNamespace(pane_id='%9', session_id='sid-9', session_path='/tmp/s.json')
    rebind_runtime(monitor, runtime, session, binding)
    assert len(upsert_calls) == 1, 'explicit zero rebind must always upsert (rollback behavior)'
    assert cache_calls == []


def test_explicit_zero_full_lifecycle_smoke(tmp_path: Path) -> None:
    """One pass through registry + keeper + mount with explicit zero; nothing should crash."""
    runtime_store = _RecordingRuntimeStore()
    registry = AgentRegistry(PathLayout(tmp_path), _config(), runtime_store=runtime_store)
    registry.upsert(_runtime())
    cached = registry.get('agent1')
    assert cached is not None

    inner_keeper = _CountingJsonStore()
    keeper_store = KeeperStateStore(PathLayout(tmp_path), store=inner_keeper)
    keeper_store.save(KeeperState(
        project_id='proj-1', keeper_pid=555, started_at='2026-04-30T00:00:00Z',
        last_check_at='2026-04-30T00:00:00Z', state='running', restart_count=0,
        last_restart_at='2026-04-30T00:00:00Z',
    ))

    inner_mount = _CountingJsonStore()
    mgr = MountManager(
        PathLayout(tmp_path), store=inner_mount,
        clock=lambda: '2026-04-30T00:00:00Z',
        uid_getter=lambda: 1000, boot_id_getter=lambda: 'b',
    )
    mgr.mark_mounted(project_id='proj-1', pid=1234, socket_path='/tmp/sock', generation=1)
    mgr.refresh_heartbeat()

    # All three writers persisted (explicit rollback/off behavior)
    assert len(runtime_store.save_calls) >= 1
    assert len(inner_keeper.save_calls) >= 1
    assert len(inner_mount.save_calls) >= 2
