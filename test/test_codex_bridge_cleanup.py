from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import provider_backends.codex.launcher_runtime.bridge as bridge


class _FakePopen:
    def __init__(self, pid: int, calls: list[tuple[list[str], dict[str, object]]]):
        self.pid = pid
        self._calls = calls

    def __call__(self, args, **kwargs):
        self._calls.append((args, kwargs))
        return self


def _patch_basic_spawn(monkeypatch, pid: int = 2000) -> list[tuple[list[str], dict[str, object]]]:
    calls: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(bridge, 'bridge_runtime_env', lambda runtime_dir: {})
    monkeypatch.setattr(bridge.subprocess, 'Popen', _FakePopen(pid, calls))
    return calls


def test_cleanup_terminates_existing_bridge(tmp_path: Path, monkeypatch) -> None:
    runtime_dir = tmp_path / 'runtime'
    runtime_dir.mkdir()
    popen_calls = _patch_basic_spawn(monkeypatch, pid=456)
    terminate_calls: list[tuple[int, float, object]] = []

    def fake_terminate(pid: int, *, timeout_s: float, is_pid_alive_fn):
        terminate_calls.append((pid, timeout_s, is_pid_alive_fn))
        return True

    monkeypatch.setattr(bridge, 'read_pid_file', lambda path: 123)
    monkeypatch.setattr(bridge, '_argv_matches_bridge', lambda pid, runtime_dir_str: True)
    monkeypatch.setattr(bridge, '_read_starttime', lambda pid: 999)
    monkeypatch.setattr(bridge, 'terminate_pid_tree', fake_terminate)

    bridge.spawn_codex_bridge(runtime_dir=runtime_dir, pane_id='%1')

    assert terminate_calls[0][0] == 123
    assert terminate_calls[0][1] == 1.0
    assert callable(terminate_calls[0][2])
    assert len(popen_calls) == 1
    assert (runtime_dir / 'bridge.pid').read_text(encoding='utf-8').strip() == '456'


def test_cleanup_skips_when_no_pid_file(tmp_path: Path, monkeypatch) -> None:
    runtime_dir = tmp_path / 'runtime'
    runtime_dir.mkdir()
    popen_calls = _patch_basic_spawn(monkeypatch, pid=457)
    terminate_calls: list[int] = []

    monkeypatch.setattr(bridge, 'read_pid_file', lambda path: None)
    monkeypatch.setattr(
        bridge,
        'terminate_pid_tree',
        lambda pid, **kwargs: terminate_calls.append(pid),
    )

    bridge.spawn_codex_bridge(runtime_dir=runtime_dir, pane_id='%1')

    assert terminate_calls == []
    assert len(popen_calls) == 1


def test_cleanup_skips_invalid_argv(tmp_path: Path, monkeypatch) -> None:
    runtime_dir = tmp_path / 'runtime'
    runtime_dir.mkdir()
    _patch_basic_spawn(monkeypatch, pid=458)
    terminate_calls: list[int] = []

    monkeypatch.setattr(bridge, 'read_pid_file', lambda path: 123)
    monkeypatch.setattr(bridge, '_argv_matches_bridge', lambda pid, runtime_dir_str: False)
    monkeypatch.setattr(
        bridge,
        'terminate_pid_tree',
        lambda pid, **kwargs: terminate_calls.append(pid),
    )

    bridge.spawn_codex_bridge(runtime_dir=runtime_dir, pane_id='%1')

    assert terminate_calls == []


def test_cleanup_skips_runtime_dir_mismatch(tmp_path: Path, monkeypatch) -> None:
    runtime_dir = tmp_path / 'runtime'
    runtime_dir.mkdir()
    _patch_basic_spawn(monkeypatch, pid=459)
    terminate_calls: list[int] = []

    raw_cmdline = (
        b'python\x00-m\x00provider_backends.codex.bridge\x00'
        b'--runtime-dir\x00/tmp/other-runtime\x00'
    )

    def fake_read_bytes(path: Path) -> bytes:
        assert path == Path('/proc/123/cmdline')
        return raw_cmdline

    monkeypatch.setattr(bridge, 'read_pid_file', lambda path: 123)
    monkeypatch.setattr(Path, 'read_bytes', fake_read_bytes)
    monkeypatch.setattr(
        bridge,
        'terminate_pid_tree',
        lambda pid, **kwargs: terminate_calls.append(pid),
    )

    bridge.spawn_codex_bridge(runtime_dir=runtime_dir, pane_id='%1')

    assert terminate_calls == []


def test_guarded_alive_blocks_sigkill_on_starttime_change(tmp_path: Path, monkeypatch) -> None:
    runtime_dir = tmp_path / 'runtime'
    runtime_dir.mkdir()
    guarded_results: list[bool] = []
    starttimes = iter([111, 222])

    def fake_terminate(pid: int, *, timeout_s: float, is_pid_alive_fn):
        guarded_results.append(is_pid_alive_fn(pid))
        return True

    monkeypatch.setattr(bridge, 'read_pid_file', lambda path: 123)
    monkeypatch.setattr(bridge, '_argv_matches_bridge', lambda pid, runtime_dir_str: True)
    monkeypatch.setattr(bridge, '_read_starttime', lambda pid: next(starttimes))
    monkeypatch.setattr(bridge, 'is_pid_alive', lambda pid: True)
    monkeypatch.setattr(bridge, 'terminate_pid_tree', fake_terminate)

    bridge._terminate_existing_bridge_impl(runtime_dir)

    assert guarded_results == [False]


def test_guarded_alive_blocks_sigkill_on_runtime_mismatch(tmp_path: Path, monkeypatch) -> None:
    runtime_dir = tmp_path / 'runtime'
    runtime_dir.mkdir()
    guarded_results: list[bool] = []
    argv_matches = iter([True, False])

    def fake_terminate(pid: int, *, timeout_s: float, is_pid_alive_fn):
        guarded_results.append(is_pid_alive_fn(pid))
        return True

    monkeypatch.setattr(bridge, 'read_pid_file', lambda path: 123)
    monkeypatch.setattr(bridge, '_argv_matches_bridge', lambda pid, runtime_dir_str: next(argv_matches))
    monkeypatch.setattr(bridge, '_read_starttime', lambda pid: None)
    monkeypatch.setattr(bridge, 'is_pid_alive', lambda pid: True)
    monkeypatch.setattr(bridge, 'terminate_pid_tree', fake_terminate)

    bridge._terminate_existing_bridge_impl(runtime_dir)

    assert guarded_results == [False]


def test_cleanup_exception_does_not_block_spawn(tmp_path: Path, monkeypatch) -> None:
    runtime_dir = tmp_path / 'runtime'
    runtime_dir.mkdir()
    popen_calls = _patch_basic_spawn(monkeypatch, pid=460)

    def fake_terminate(pid: int, *, timeout_s: float, is_pid_alive_fn):
        raise OSError('boom')

    monkeypatch.setattr(bridge, 'read_pid_file', lambda path: 123)
    monkeypatch.setattr(bridge, '_argv_matches_bridge', lambda pid, runtime_dir_str: True)
    monkeypatch.setattr(bridge, '_read_starttime', lambda pid: 999)
    monkeypatch.setattr(bridge, 'terminate_pid_tree', fake_terminate)

    bridge.spawn_codex_bridge(runtime_dir=runtime_dir, pane_id='%1')

    assert len(popen_calls) == 1
    assert '[bridge cleanup] unexpected: boom' in (runtime_dir / 'bridge.stderr.log').read_text(encoding='utf-8')


def test_spawn_acquires_file_lock(tmp_path: Path, monkeypatch) -> None:
    runtime_dir = tmp_path / 'runtime'
    runtime_dir.mkdir()
    events: list[str] = []
    original_write_text = Path.write_text

    @contextmanager
    def fake_file_lock(path: Path):
        assert path == runtime_dir / 'bridge.lock'
        events.append('lock_enter')
        try:
            yield
        finally:
            events.append('lock_exit')

    class FakePopen:
        pid = 461

        def __init__(self, args, **kwargs):
            events.append('popen')

    def fake_write_text(path: Path, data: str, *args, **kwargs):
        if path == runtime_dir / 'bridge.pid':
            events.append('pid_write')
        return original_write_text(path, data, *args, **kwargs)

    monkeypatch.setattr(bridge, 'bridge_runtime_env', lambda runtime_dir: {})
    monkeypatch.setattr(bridge, 'file_lock', fake_file_lock)
    monkeypatch.setattr(bridge, '_terminate_existing_bridge', lambda runtime_dir, stderr_log: events.append('cleanup'))
    monkeypatch.setattr(bridge.subprocess, 'Popen', FakePopen)
    monkeypatch.setattr(Path, 'write_text', fake_write_text)

    bridge.spawn_codex_bridge(runtime_dir=runtime_dir, pane_id='%1')

    assert events == ['lock_enter', 'cleanup', 'popen', 'pid_write', 'lock_exit']


def test_sequential_double_spawn_keeps_one_bridge(tmp_path: Path, monkeypatch) -> None:
    runtime_dir = tmp_path / 'runtime'
    runtime_dir.mkdir()
    (runtime_dir / 'bridge.pid').write_text('100\n', encoding='utf-8')
    popen_pids = iter([101, 102])
    terminate_calls: list[int] = []

    class FakePopen:
        def __init__(self, args, **kwargs):
            self.pid = next(popen_pids)

    def fake_terminate(pid: int, *, timeout_s: float, is_pid_alive_fn):
        assert is_pid_alive_fn(pid) is True
        terminate_calls.append(pid)
        return True

    monkeypatch.setattr(bridge, 'bridge_runtime_env', lambda runtime_dir: {})
    monkeypatch.setattr(bridge, '_argv_matches_bridge', lambda pid, runtime_dir_str: runtime_dir_str == str(runtime_dir))
    monkeypatch.setattr(bridge, '_read_starttime', lambda pid: pid * 10)
    monkeypatch.setattr(bridge, 'is_pid_alive', lambda pid: True)
    monkeypatch.setattr(bridge, 'terminate_pid_tree', fake_terminate)
    monkeypatch.setattr(bridge.subprocess, 'Popen', FakePopen)

    bridge.spawn_codex_bridge(runtime_dir=runtime_dir, pane_id='%1')
    assert terminate_calls == [100]
    assert (runtime_dir / 'bridge.pid').read_text(encoding='utf-8').strip() == '101'

    bridge.spawn_codex_bridge(runtime_dir=runtime_dir, pane_id='%1')
    assert terminate_calls == [100, 101]
    assert (runtime_dir / 'bridge.pid').read_text(encoding='utf-8').strip() == '102'
