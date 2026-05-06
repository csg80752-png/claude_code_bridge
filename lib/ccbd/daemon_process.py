from __future__ import annotations

from pathlib import Path
import json
import os
import subprocess
import sys
import time

from ccbd.socket_client import CcbdClient, CcbdClientError


class CcbdProcessError(RuntimeError):
    pass


def spawn_ccbd_process(
    *,
    project_root: Path,
    socket_path: Path,
    ccbd_dir: Path,
    timeout_s: float,
    keeper_pid: int | None = None,
) -> None:
    script = Path(__file__).resolve().parent / 'main.py'
    env = _ccbd_env(keeper_pid=keeper_pid)
    ccbd_dir.mkdir(parents=True, exist_ok=True)
    stdout_log = open(ccbd_dir / 'ccbd.stdout.log', 'ab')
    stderr_log = open(ccbd_dir / 'ccbd.stderr.log', 'ab')
    process = subprocess.Popen(
        [sys.executable, str(script), '--project', str(project_root)],
        cwd=str(project_root),
        env=env,
        stdout=stdout_log,
        stderr=stderr_log,
        start_new_session=True,
    )
    _wait_for_ccbd_ready(
        process=process,
        socket_path=socket_path,
        lease_path=ccbd_dir / 'lease.json',
        timeout_s=timeout_s,
    )


def _wait_for_ccbd_ready(
    *,
    process: subprocess.Popen[bytes],
    socket_path: Path,
    lease_path: Path,
    timeout_s: float,
) -> None:
    deadline = time.time() + max(0.0, float(timeout_s))
    last_error: str | None = None
    while time.time() < deadline:
        if socket_path.exists():
            try:
                CcbdClient(socket_path, timeout_s=0.2).ping('ccbd')
                if _lease_belongs_to_process(lease_path=lease_path, process_pid=process.pid):
                    return
                last_error = f'ccbd socket is served by another process, expected pid {process.pid}'
            except CcbdClientError as exc:
                last_error = str(exc)
        if process.poll() is not None:
            raise CcbdProcessError(f'ccbd exited before ready with code {process.returncode}')
        time.sleep(0.05)
    raise CcbdProcessError(last_error or 'timed out waiting for ccbd to become ready')


def _lease_belongs_to_process(*, lease_path: Path, process_pid: int) -> bool:
    try:
        record = json.loads(lease_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return False
    try:
        return int(record.get('ccbd_pid') or 0) == int(process_pid)
    except (TypeError, ValueError):
        return False


def _ccbd_env(*, keeper_pid: int | None) -> dict[str, str]:
    env = dict(os.environ)
    env['PYTHONUNBUFFERED'] = '1'
    lib_root = str(Path(__file__).resolve().parents[1])
    current_pythonpath = env.get('PYTHONPATH')
    env['PYTHONPATH'] = lib_root if not current_pythonpath else lib_root + os.pathsep + current_pythonpath
    if keeper_pid is not None and keeper_pid > 0:
        env['CCB_KEEPER_PID'] = str(int(keeper_pid))
    return env


__all__ = ['CcbdProcessError', 'spawn_ccbd_process']
