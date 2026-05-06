from __future__ import annotations

from pathlib import Path

from .stores import report_summary_fields, safe_report_load


def ccbd_summary(*, local, stores: dict[str, object], errors: list[str], installation: dict[str, object] | None = None) -> dict:
    daemon_install_path = _daemon_install_path(getattr(local, 'pid', None))
    current_install_path = _normalized_path((installation or {}).get('path'))
    return {
        'state': local.mount_state,
        'pid': local.pid,
        'daemon_install_path': str(daemon_install_path) if daemon_install_path is not None else None,
        'daemon_install_matches_current': (
            daemon_install_path == current_install_path
            if daemon_install_path is not None and current_install_path is not None
            else None
        ),
        'socket_path': local.socket_path,
        'generation': local.generation,
        'health': local.health,
        'last_heartbeat_at': local.last_heartbeat_at,
        'pid_alive': local.pid_alive,
        'socket_connectable': local.socket_connectable,
        'heartbeat_fresh': local.heartbeat_fresh,
        'takeover_allowed': local.takeover_allowed,
        'reason': local.reason,
        **stores['execution_state'].summary(),
        **report_summary_fields(safe_report_load(stores['restore_report'].load, errors, label='restore_report')),
        **report_summary_fields(safe_report_load(stores['startup_report'].load, errors, label='startup_report')),
        **report_summary_fields(safe_report_load(stores['shutdown_report'].load, errors, label='shutdown_report')),
        **report_summary_fields(safe_report_load(stores['namespace_state'].load, errors, label='namespace_state')),
        **report_summary_fields(safe_report_load(stores['namespace_event'].load_latest, errors, label='namespace_event')),
        **report_summary_fields(safe_report_load(stores['start_policy'].load, errors, label='start_policy')),
        **report_summary_fields(safe_report_load(stores['tmux_cleanup'].load_latest, errors, label='tmux_cleanup')),
        'diagnostic_errors': errors,
    }


def _daemon_install_path(pid: int | None) -> Path | None:
    if pid is None or int(pid) <= 0:
        return None
    try:
        raw = Path(f'/proc/{int(pid)}/cmdline').read_bytes()
    except OSError:
        return None
    for token in raw.split(b'\0'):
        if not token:
            continue
        try:
            path = Path(token.decode()).expanduser()
        except UnicodeDecodeError:
            continue
        if path.name == 'main.py' and path.parent.name == 'ccbd' and path.parent.parent.name == 'lib':
            return path.parent.parent.parent.resolve()
    return None


def _normalized_path(value: object) -> Path | None:
    if value is None:
        return None
    try:
        return Path(str(value)).expanduser().resolve()
    except OSError:
        return None


__all__ = ['ccbd_summary']
