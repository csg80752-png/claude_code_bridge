from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Mapping

from .processes import terminate_pid_tree

CCBD_RUNTIME_NAME = "ccbd"
CCBD_RPC_PREFIX = "ask"
CCBD_STATE_FILE_NAME = "ccbd.json"


def terminate_provider_daemon(
    provider: str,
    *,
    specs_by_provider: Mapping[str, object],
    state_file_path_fn: Callable[[str], Path],
    shutdown_daemon_fn: Callable[[str, float, Path], bool],
    read_state_fn: Callable[[Path], dict | None],
    kill_pid_fn: Callable[[int], bool] | Callable[[int, bool], bool],
) -> None:
    spec = specs_by_provider.get(provider)
    if spec is None:
        return

    state_file = state_file_path_fn(CCBD_STATE_FILE_NAME)
    try:
        if shutdown_daemon_fn(CCBD_RPC_PREFIX, 1.0, state_file):
            print(f"✅ {CCBD_RUNTIME_NAME} runtime shutdown requested")
            return
        state = read_state_fn(state_file)
        if state and state.get("pid"):
            pid = int(state["pid"])
            if kill_pid_fn(pid, force=True):
                print(f"✅ {CCBD_RUNTIME_NAME} runtime force killed (pid={pid})")
            else:
                print(f"⚠️ {CCBD_RUNTIME_NAME} runtime could not be killed (pid={pid})")
    except Exception:
        pass


def kill_project_ccbd_daemons(
    *,
    project_root: Path,
    terminate_pid_tree_fn: Callable[[int], bool] | None = None,
) -> int:
    terminate_pid_tree_fn = terminate_pid_tree_fn or (lambda pid: terminate_pid_tree(pid))
    killed = 0
    for pid in find_project_ccbd_daemon_pids(project_root=project_root):
        if terminate_pid_tree_fn(pid):
            killed += 1
    if killed:
        print(f"✅ {CCBD_RUNTIME_NAME} orphan daemon cleanup killed {killed} process(es)")
    return killed


def find_project_ccbd_daemon_pids(*, project_root: Path, proc_root: Path = Path('/proc')) -> tuple[int, ...]:
    project = _normalized_path(project_root)
    if project is None or os.name == 'nt' or not proc_root.exists():
        return ()
    current_pid = os.getpid()
    pids: list[int] = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == current_pid:
            continue
        cmdline = _read_cmdline(entry / 'cmdline')
        if _is_project_ccbd_main(cmdline, project_root=project):
            pids.append(pid)
    return tuple(sorted(pids))


def _read_cmdline(path: Path) -> tuple[str, ...]:
    try:
        raw = path.read_bytes()
    except OSError:
        return ()
    tokens: list[str] = []
    for token in raw.split(b'\0'):
        if not token:
            continue
        try:
            tokens.append(token.decode())
        except UnicodeDecodeError:
            continue
    return tuple(tokens)


def _is_project_ccbd_main(cmdline: tuple[str, ...], *, project_root: Path) -> bool:
    if not cmdline:
        return False
    if not any(_is_ccbd_main_token(token) for token in cmdline):
        return False
    for index, token in enumerate(cmdline):
        if token == '--project' and index + 1 < len(cmdline):
            return _normalized_path(Path(cmdline[index + 1])) == project_root
        if token.startswith('--project='):
            return _normalized_path(Path(token.split('=', 1)[1])) == project_root
    return False


def _is_ccbd_main_token(token: str) -> bool:
    path = Path(token)
    return path.name == 'main.py' and path.parent.name == 'ccbd' and path.parent.parent.name == 'lib'


def _normalized_path(path: Path) -> Path | None:
    try:
        return path.expanduser().resolve()
    except OSError:
        return None


__all__ = ["find_project_ccbd_daemon_pids", "kill_project_ccbd_daemons", "terminate_provider_daemon"]
