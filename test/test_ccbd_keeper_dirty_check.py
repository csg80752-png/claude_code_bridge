from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest

from ccbd.keeper_runtime.records import KeeperState
from ccbd.keeper_runtime.stores import KeeperStateStore
from storage.paths import PathLayout


class _RecordingJsonStore:
    def __init__(self) -> None:
        self.save_calls: list[tuple[Path, Any]] = []

    def save(self, path: Path, value: Any, serializer: Callable[[Any], dict] | None = None) -> None:
        self.save_calls.append((path, value))

    def load(self, path: Path, *, loader=None):
        return None


def _layout(tmp_path: Path) -> PathLayout:
    return PathLayout(tmp_path)


def _state(
    last_check_at: str = '2026-04-30T00:00:00Z',
    state: str = 'running',
    restart_count: int = 0,
    last_failure_reason: str | None = None,
) -> KeeperState:
    return KeeperState(
        project_id='proj-1',
        keeper_pid=555,
        started_at='2026-04-30T00:00:00Z',
        last_check_at=last_check_at,
        state=state,
        restart_count=restart_count,
        last_restart_at='2026-04-30T00:00:00Z',
        last_failure_reason=last_failure_reason,
    )


def _install_fake_clock(monkeypatch: pytest.MonkeyPatch, values: list[float]) -> None:
    """Replace time.monotonic in stores module with a fake that returns values in order."""
    import ccbd.keeper_runtime.stores as stores_mod

    iterator = iter(values)
    fake = type('FakeTime', (), {'monotonic': staticmethod(lambda: next(iterator))})
    monkeypatch.setattr(stores_mod, 'time', fake, raising=False)


def test_explicit_zero_saves_every_call(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv('CCB_CCBD_DIRTY_CHECK', '0')
    inner = _RecordingJsonStore()
    store = KeeperStateStore(_layout(tmp_path), store=inner)

    store.save(_state())
    store.save(_state(last_check_at='2026-04-30T00:00:01Z'))
    store.save(_state(last_check_at='2026-04-30T00:00:02Z'))

    assert len(inner.save_calls) == 3, 'explicit zero preserves rollback save-every-call'


def test_unset_env_defaults_to_heartbeat_suppression(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv('CCB_CCBD_DIRTY_CHECK', raising=False)
    inner = _RecordingJsonStore()
    store = KeeperStateStore(_layout(tmp_path), store=inner)

    store.save(_state())
    store.save(_state(last_check_at='2026-04-30T00:00:01Z'))
    store.save(_state(last_check_at='2026-04-30T00:00:02Z'))

    assert len(inner.save_calls) == 1, 'unset env must keep production keeper dirty-check enabled'


def test_flag_on_heartbeat_only_suppressed_within_5s(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Multiple last_check_at-only saves within 5s collapse to a single disk write."""
    monkeypatch.setenv('CCB_CCBD_DIRTY_CHECK', '1')
    inner = _RecordingJsonStore()
    store = KeeperStateStore(_layout(tmp_path), store=inner)

    # Real time: all five saves happen within microseconds.
    store.save(_state())
    store.save(_state(last_check_at='2026-04-30T00:00:01Z'))
    store.save(_state(last_check_at='2026-04-30T00:00:02Z'))
    store.save(_state(last_check_at='2026-04-30T00:00:03Z'))
    store.save(_state(last_check_at='2026-04-30T00:00:04Z'))

    assert len(inner.save_calls) == 1, f'expected 1 save (initial only); got {len(inner.save_calls)}'


def test_flag_on_meaningful_state_change_persists_immediately(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv('CCB_CCBD_DIRTY_CHECK', '1')
    inner = _RecordingJsonStore()
    store = KeeperStateStore(_layout(tmp_path), store=inner)

    store.save(_state())  # initial
    store.save(_state(last_check_at='2026-04-30T00:00:01Z', state='restarting'))
    store.save(_state(last_check_at='2026-04-30T00:00:01Z', state='restarting', restart_count=1))

    assert len(inner.save_calls) == 3, 'meaningful changes must persist immediately'


def test_flag_on_forced_flush_after_5s(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When monotonic clock advances >=5s, even heartbeat-only changes flush."""
    monkeypatch.setenv('CCB_CCBD_DIRTY_CHECK', '1')
    # Each save can call monotonic up to 2x (check + update on flush). Provide a generous
    # cyclic clock that progresses through the relevant intervals.
    _install_fake_clock(monkeypatch, [0.0, 4.0, 6.0, 6.0])

    inner = _RecordingJsonStore()
    store = KeeperStateStore(_layout(tmp_path), store=inner)

    store.save(_state())  # initial — uses 1 monotonic to set _last_flush_at (t=0)
    store.save(_state(last_check_at='2026-04-30T00:00:04Z'))  # check at t=4 — suppressed
    store.save(_state(last_check_at='2026-04-30T00:00:06Z'))  # check at t=6 — flushed; sets _last_flush_at=6.0

    assert len(inner.save_calls) == 2


def test_flag_on_failure_reason_change_persists(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv('CCB_CCBD_DIRTY_CHECK', '1')
    inner = _RecordingJsonStore()
    store = KeeperStateStore(_layout(tmp_path), store=inner)

    store.save(_state())
    store.save(_state(last_check_at='2026-04-30T00:00:01Z', last_failure_reason='socket_unreachable'))

    assert len(inner.save_calls) == 2, 'last_failure_reason change must persist immediately'
