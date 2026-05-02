from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from cli.kill_runtime.processes import is_pid_alive, terminate_pid_tree
from provider_profiles import load_resolved_provider_profile
from runtime_pid_cleanup.procfs import read_pid_file
from storage.locks import file_lock

from .command import prepare_codex_home_overrides
from .session_paths import session_file_for_runtime_dir


def post_launch(backend: object, pane_id: str, runtime_dir: Path, launch_session_id: str, prepared_state: dict[str, object]) -> None:
    del launch_session_id
    del prepared_state
    write_pane_pid(backend, pane_id, runtime_dir / 'codex.pid')
    spawn_codex_bridge(runtime_dir=runtime_dir, pane_id=pane_id)


def _read_starttime(pid: int) -> int | None:
    """/proc/<pid>/stat field 22 (starttime, clock ticks). PID 동일성 검증용."""
    try:
        raw = Path(f'/proc/{pid}/stat').read_text(encoding='utf-8')
        after_comm = raw.rsplit(')', 1)[-1].split()
        return int(after_comm[19])
    except Exception:
        return None


def _argv_matches_bridge(pid: int, runtime_dir_str: str) -> bool:
    """/proc/<pid>/cmdline의 NUL-split argv가 정확히 codex bridge + 해당 runtime_dir인지 검증."""
    try:
        raw = Path(f'/proc/{pid}/cmdline').read_bytes()
    except Exception:
        return False
    if not raw:
        return False
    argv = raw.split(b'\x00')
    while argv and argv[-1] == b'':
        argv.pop()
    try:
        decoded = [a.decode('utf-8', errors='replace') for a in argv]
    except Exception:
        return False
    has_module = any(
        decoded[i] == '-m' and i + 1 < len(decoded) and decoded[i + 1] == 'provider_backends.codex.bridge'
        for i in range(len(decoded))
    )
    if not has_module:
        return False
    has_runtime = any(
        decoded[i] == '--runtime-dir' and i + 1 < len(decoded) and decoded[i + 1] == runtime_dir_str
        for i in range(len(decoded))
    )
    return has_runtime


def _terminate_existing_bridge(runtime_dir: Path, stderr_log) -> None:
    """Best-effort 기존 bridge cleanup. 모든 예외 흡수."""
    try:
        _terminate_existing_bridge_impl(runtime_dir)
    except Exception as exc:
        try:
            stderr_log.write(f'[bridge cleanup] unexpected: {exc}\n'.encode())
            stderr_log.flush()
        except Exception:
            pass


def _terminate_existing_bridge_impl(runtime_dir: Path) -> None:
    pid = read_pid_file(runtime_dir / 'bridge.pid')
    if pid is None:
        return
    runtime_dir_str = str(runtime_dir)
    if not _argv_matches_bridge(pid, runtime_dir_str):
        return
    starttime_pre = _read_starttime(pid)

    def _guarded_alive(p: int) -> bool:
        if not is_pid_alive(p):
            return False
        if starttime_pre is not None:
            starttime_now = _read_starttime(p)
            if starttime_now != starttime_pre:
                return False
        if not _argv_matches_bridge(p, runtime_dir_str):
            return False
        return True

    terminate_pid_tree(pid, timeout_s=1.0, is_pid_alive_fn=_guarded_alive)


def spawn_codex_bridge(*, runtime_dir: Path, pane_id: str) -> None:
    with file_lock(runtime_dir / 'bridge.lock'):
        stderr_log = open(runtime_dir / 'bridge.stderr.log', 'ab')
        _terminate_existing_bridge(runtime_dir, stderr_log)

        env = os.environ.copy()
        env['CODEX_TERMINAL'] = 'tmux'
        env['CODEX_TMUX_SESSION'] = pane_id
        env['CODEX_RUNTIME_DIR'] = str(runtime_dir)
        env['CODEX_INPUT_FIFO'] = str(runtime_dir / 'input.fifo')
        env['CODEX_OUTPUT_FIFO'] = str(runtime_dir / 'output.fifo')
        env['CODEX_TMUX_LOG'] = str(runtime_dir / 'bridge_output.log')
        env.update(bridge_runtime_env(runtime_dir))
        existing_pythonpath = env.get('PYTHONPATH', '')
        lib_root = str(Path(__file__).resolve().parents[3])
        env['PYTHONPATH'] = lib_root if not existing_pythonpath else lib_root + os.pathsep + existing_pythonpath
        stdout_log = open(runtime_dir / 'bridge.stdout.log', 'ab')
        proc = subprocess.Popen(
            [sys.executable, '-m', 'provider_backends.codex.bridge', '--runtime-dir', str(runtime_dir)],
            env=env,
            stdout=stdout_log,
            stderr=stderr_log,
            start_new_session=True,
        )
        (runtime_dir / 'bridge.pid').write_text(f'{proc.pid}\n', encoding='utf-8')


def bridge_runtime_env(runtime_dir: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    session_file = session_file_for_runtime_dir(runtime_dir)
    if session_file is not None:
        env['CCB_SESSION_FILE'] = str(session_file)
    profile = load_resolved_provider_profile(runtime_dir)
    env.update(prepare_codex_home_overrides(runtime_dir, profile))
    return env


def write_pane_pid(backend: object, pane_id: str, path: Path) -> None:
    try:
        result = backend._tmux_run(  # type: ignore[attr-defined]
            ['display-message', '-p', '-t', pane_id, '#{pane_pid}'],
            capture=True,
            timeout=1.0,
        )
    except Exception:
        return
    pane_pid = (result.stdout or '').strip()
    if pane_pid.isdigit():
        path.write_text(f'{pane_pid}\n', encoding='utf-8')


__all__ = ['post_launch', 'spawn_codex_bridge', 'write_pane_pid']
