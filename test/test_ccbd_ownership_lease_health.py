from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest

from ccbd.models import LeaseHealth, MountState
from ccbd.services.mount import MountManager
from ccbd.services.ownership import OwnershipGuard
from storage.json_store import JsonStore
from storage.paths import PathLayout


class _CountingJsonStore:
    def __init__(self) -> None:
        self._inner = JsonStore()
        self.save_calls: list[Path] = []

    def save(self, path: Path, value: Any, serializer: Callable[[Any], dict] | None = None) -> None:
        self.save_calls.append(path)
        self._inner.save(path, value, serializer=serializer)

    def load(self, path: Path, *, loader=None):
        return self._inner.load(path, loader=loader)


def _layout(tmp_path: Path) -> PathLayout:
    return PathLayout(tmp_path)


def _install_fake_mount_clock(monkeypatch: pytest.MonkeyPatch, values: list[float]) -> None:
    import ccbd.services.mount as mount_mod

    iterator = iter(values)
    fake = type('FakeTime', (), {'monotonic': staticmethod(lambda: next(iterator))})
    monkeypatch.setattr(mount_mod, 'time', fake, raising=False)


def test_flag_on_inspect_remains_healthy_during_suppression_window(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Under flag-on, lease.json may be up to 5s stale; OwnershipGuard reports HEALTHY because the 15s grace covers it."""
    monkeypatch.setenv('CCB_CCBD_DIRTY_CHECK', '1')
    inner = _CountingJsonStore()

    mount_clock = iter([
        '2026-04-30T00:00:00Z',  # mark_mounted
        '2026-04-30T00:00:01Z',  # refresh #1 (suppressed; in-memory only)
        '2026-04-30T00:00:02Z',  # refresh #2 (suppressed)
        '2026-04-30T00:00:04Z',  # refresh #3 (suppressed; still <5s)
    ])
    mgr = MountManager(
        _layout(tmp_path),
        store=inner,
        clock=lambda: next(mount_clock),
        uid_getter=lambda: 1000,
        boot_id_getter=lambda: 'boot-id',
    )
    mgr.mark_mounted(project_id='proj-1', pid=1234, socket_path='/tmp/sock', generation=1)
    mgr.refresh_heartbeat()
    mgr.refresh_heartbeat()
    mgr.refresh_heartbeat()

    # Disk lease still has last_heartbeat_at=00:00:00 (only mark_mounted persisted under flag).
    on_disk = mgr.load_state()
    assert on_disk is not None
    assert on_disk.last_heartbeat_at == '2026-04-30T00:00:00Z'
    assert len(inner.save_calls) == 1, 'flag-on must suppress refresh writes within 5s'

    guard = OwnershipGuard(
        _layout(tmp_path),
        mgr,
        clock=lambda: '2026-04-30T00:00:04Z',  # 4s after last disk heartbeat
        pid_exists=lambda pid: True,
        socket_probe=lambda path: True,
    )
    inspection = guard.inspect(on_disk)
    assert inspection.heartbeat_fresh is True, '4s old disk lease must be fresh under 15s grace'
    assert inspection.health is LeaseHealth.HEALTHY


def test_flag_on_disk_heartbeat_refreshes_before_15s_grace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Forced flush at 5s must rewrite lease.json well before the 15s freshness grace expires."""
    monkeypatch.setenv('CCB_CCBD_DIRTY_CHECK', '1')
    # mark_mounted (1 call: set flush time) + refresh (check + flush = 2 calls) = 3 calls
    _install_fake_mount_clock(monkeypatch, [0.0, 5.5, 5.5])

    inner = _CountingJsonStore()
    mount_clock = iter(['2026-04-30T00:00:00Z', '2026-04-30T00:00:06Z'])
    mgr = MountManager(
        _layout(tmp_path),
        store=inner,
        clock=lambda: next(mount_clock),
        uid_getter=lambda: 1000,
        boot_id_getter=lambda: 'boot-id',
    )

    mgr.mark_mounted(project_id='proj-1', pid=1234, socket_path='/tmp/sock', generation=1)
    refreshed = mgr.refresh_heartbeat()  # t=5.5 monotonic — flush due

    on_disk = mgr.load_state()
    assert on_disk is not None
    assert on_disk.last_heartbeat_at == '2026-04-30T00:00:06Z', 'forced flush must rewrite disk lease'
    assert len(inner.save_calls) == 2

    # Now, 9s after the fresh disk write, inspection still healthy (well under 15s grace).
    guard = OwnershipGuard(
        _layout(tmp_path),
        mgr,
        clock=lambda: '2026-04-30T00:00:15Z',
        pid_exists=lambda pid: True,
        socket_probe=lambda path: True,
    )
    inspection = guard.inspect(on_disk)
    assert inspection.heartbeat_fresh is True


def test_explicit_zero_baseline_inspect_healthy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Sanity: with rollback behavior, every refresh writes — disk lease is always within 1s of clock."""
    monkeypatch.setenv('CCB_CCBD_DIRTY_CHECK', '0')
    inner = _CountingJsonStore()
    mount_clock = iter(['2026-04-30T00:00:00Z', '2026-04-30T00:00:01Z', '2026-04-30T00:00:02Z'])
    mgr = MountManager(
        _layout(tmp_path),
        store=inner,
        clock=lambda: next(mount_clock),
        uid_getter=lambda: 1000,
        boot_id_getter=lambda: 'boot-id',
    )
    mgr.mark_mounted(project_id='proj-1', pid=1234, socket_path='/tmp/sock', generation=1)
    mgr.refresh_heartbeat()
    mgr.refresh_heartbeat()
    on_disk = mgr.load_state()
    assert on_disk is not None
    assert on_disk.last_heartbeat_at == '2026-04-30T00:00:02Z'
    assert len(inner.save_calls) == 3, 'explicit zero writes every refresh'
