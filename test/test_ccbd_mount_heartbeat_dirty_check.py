from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest

from ccbd.models import CcbdLease, MountState
from ccbd.services.mount import MountManager
from storage.json_store import JsonStore
from storage.paths import PathLayout


class _CountingJsonStore:
    """Wraps a real JsonStore to count save calls while preserving disk semantics."""

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


def _manager(tmp_path: Path, *, clock_value: str = '2026-04-30T00:00:00Z') -> tuple[MountManager, _CountingJsonStore]:
    inner = _CountingJsonStore()
    mgr = MountManager(
        _layout(tmp_path),
        store=inner,
        clock=lambda: clock_value,
        uid_getter=lambda: 1000,
        boot_id_getter=lambda: 'boot-id',
    )
    return mgr, inner


def _install_fake_clock(monkeypatch: pytest.MonkeyPatch, values: list[float]) -> None:
    import ccbd.services.mount as mount_mod

    iterator = iter(values)
    fake = type('FakeTime', (), {'monotonic': staticmethod(lambda: next(iterator))})
    monkeypatch.setattr(mount_mod, 'time', fake, raising=False)


def test_explicit_zero_writes_every_heartbeat(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv('CCB_CCBD_DIRTY_CHECK', '0')
    mgr, inner = _manager(tmp_path)

    mgr.mark_mounted(project_id='proj-1', pid=1234, socket_path='/tmp/sock', generation=1)
    mgr.refresh_heartbeat()
    mgr.refresh_heartbeat()
    mgr.refresh_heartbeat()

    assert len(inner.save_calls) == 4


def test_heartbeat_persists_immediately_when_dirty_check_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv('CCB_CCBD_DIRTY_CHECK', '0')
    mgr, inner = _manager(tmp_path)

    mgr.mark_mounted(project_id='proj-1', pid=1234, socket_path='/tmp/sock', generation=1)
    inner.save_calls.clear()
    mgr.refresh_heartbeat()

    assert len(inner.save_calls) == 1


def test_unset_env_defaults_to_heartbeat_suppression(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv('CCB_CCBD_DIRTY_CHECK', raising=False)
    mgr, inner = _manager(tmp_path)

    mgr.mark_mounted(project_id='proj-1', pid=1234, socket_path='/tmp/sock', generation=1)
    mgr.refresh_heartbeat()
    mgr.refresh_heartbeat()
    mgr.refresh_heartbeat()

    assert len(inner.save_calls) == 1


def test_flag_on_refresh_heartbeat_suppressed_within_5s(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv('CCB_CCBD_DIRTY_CHECK', '1')
    mgr, inner = _manager(tmp_path)

    mgr.mark_mounted(project_id='proj-1', pid=1234, socket_path='/tmp/sock', generation=1)
    mgr.refresh_heartbeat()
    mgr.refresh_heartbeat()
    mgr.refresh_heartbeat()

    assert len(inner.save_calls) == 1, f'expected 1 save (mark_mounted only); got {len(inner.save_calls)}'


def test_heartbeat_dirty_check_skips_file_rewrite_inside_flush_interval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv('CCB_CCBD_DIRTY_CHECK', '1')
    mgr, inner = _manager(tmp_path)

    mgr.mark_mounted(project_id='proj-1', pid=1234, socket_path='/tmp/sock', generation=1)
    inner.save_calls.clear()
    mgr.refresh_heartbeat()

    assert inner.save_calls == []


def test_flag_on_refresh_returns_in_memory_lease_with_fresh_heartbeat(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv('CCB_CCBD_DIRTY_CHECK', '1')
    inner = _CountingJsonStore()
    clock_seq = iter(['2026-04-30T00:00:00Z', '2026-04-30T00:00:01Z'])
    mgr = MountManager(
        _layout(tmp_path),
        store=inner,
        clock=lambda: next(clock_seq),
        uid_getter=lambda: 1000,
        boot_id_getter=lambda: 'boot-id',
    )

    mgr.mark_mounted(project_id='proj-1', pid=1234, socket_path='/tmp/sock', generation=1)
    refreshed = mgr.refresh_heartbeat()
    assert refreshed.last_heartbeat_at == '2026-04-30T00:00:01Z', 'in-memory lease must reflect fresh clock'
    assert len(inner.save_calls) == 1, 'disk write suppressed; only mark_mounted persisted'


def test_flag_on_forced_flush_after_5s(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv('CCB_CCBD_DIRTY_CHECK', '1')
    # mark_mounted: 1 call (set flush time). refresh@4: 1 call (check, suppressed).
    # refresh@6: 1 call (check) + 1 call (set new flush time) = 4 total.
    _install_fake_clock(monkeypatch, [0.0, 4.0, 6.0, 6.0])

    mgr, inner = _manager(tmp_path)
    mgr.mark_mounted(project_id='proj-1', pid=1234, socket_path='/tmp/sock', generation=1)
    mgr.refresh_heartbeat()  # t=4 — suppressed
    mgr.refresh_heartbeat()  # t=6 — forced flush

    assert len(inner.save_calls) == 2


def test_flag_on_mark_unmounted_always_writes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv('CCB_CCBD_DIRTY_CHECK', '1')
    mgr, inner = _manager(tmp_path)

    mgr.mark_mounted(project_id='proj-1', pid=1234, socket_path='/tmp/sock', generation=1)
    mgr.refresh_heartbeat()  # suppressed
    mgr.mark_unmounted()

    assert len(inner.save_calls) == 2, 'mark_unmounted must persist regardless of flag'
    saved_path, saved_value = inner.save_calls[-1], None
    # Re-load from disk to verify final state
    final = mgr.load_state()
    assert final is not None
    assert final.mount_state is MountState.UNMOUNTED


def test_flag_on_unmounted_lease_refresh_is_pure_read(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv('CCB_CCBD_DIRTY_CHECK', '1')
    mgr, inner = _manager(tmp_path)

    mgr.mark_mounted(project_id='proj-1', pid=1234, socket_path='/tmp/sock', generation=1)
    mgr.mark_unmounted()
    inner.save_calls.clear()
    lease = mgr.refresh_heartbeat()
    assert lease.mount_state is MountState.UNMOUNTED
    assert inner.save_calls == [], 'unmounted lease refresh must not write disk'
