from __future__ import annotations

from dataclasses import dataclass
import os
import shutil
import subprocess
import time

from cli.context import CliContext
from cli.models import ParsedOpenCommand

from .daemon import connect_mounted_daemon
from .daemon_runtime import CcbdServiceError


_CONFIG_DRIFT_ERROR = 'mounted ccbd config does not match current .ccb/ccb.config'
_UNMOUNTED_ERRORS = frozenset(
    {
        'project ccbd is unmounted; run `ccb [agents...]` first',
        'project ccbd is not mounted; run `ccb [agents...]` first',
    }
)
_TRANSIENT_CONNECT_ERROR_FRAGMENTS = (
    'socket_unreachable',
    'Connection refused',
    'timed out',
    'Resource temporarily unavailable',
)
_OPEN_RECOVERY_WAIT_S = 10.0
_OPEN_RECOVERY_POLL_S = 0.05
_OPEN_ATTACH_WAIT_S = 30.0
_OPEN_ATTACH_POLL_S = 0.1


@dataclass(frozen=True)
class OpenSummary:
    project_id: str
    tmux_socket_path: str
    tmux_session_name: str


def open_project(context: CliContext, command: ParsedOpenCommand) -> OpenSummary:
    del command
    if shutil.which('tmux') is None:
        raise RuntimeError('tmux is required for `ccb open`')
    handle = _connect_attachable_daemon(context)
    client = handle.client
    if client is None:
        raise RuntimeError('project ccbd is mounted without a client connection')
    payload = _wait_for_attachable_namespace(client)
    tmux_socket_path, tmux_session_name, workspace_window_name = _attach_payload_fields(payload)
    env = dict(os.environ)
    env.pop('TMUX', None)
    env.pop('TMUX_PANE', None)
    if not _wait_for_tmux_session(tmux_socket_path, tmux_session_name, env=env):
        raise RuntimeError('project namespace session is missing; run `ccb` first')
    if workspace_window_name and not _wait_for_tmux_window(
        tmux_socket_path,
        f'{tmux_session_name}:{workspace_window_name}',
        env=env,
    ):
        raise RuntimeError('project namespace workspace window is missing; run `ccb` first')
    _attach_tmux_session(tmux_socket_path, tmux_session_name, env=env)
    return OpenSummary(
        project_id=context.project.project_id,
        tmux_socket_path=tmux_socket_path,
        tmux_session_name=tmux_session_name,
    )


def _connect_attachable_daemon(context: CliContext):
    deadline = time.time() + _OPEN_RECOVERY_WAIT_S
    observed_config_drift = False
    while True:
        try:
            return connect_mounted_daemon(context, allow_restart_stale=False)
        except CcbdServiceError as exc:
            message = str(exc)
            retryable = False
            if message == _CONFIG_DRIFT_ERROR:
                observed_config_drift = True
                retryable = True
            elif observed_config_drift and message in _UNMOUNTED_ERRORS:
                retryable = True
            elif _is_transient_open_connect_error(message, exc=exc):
                retryable = True
            if not retryable or time.time() >= deadline:
                raise
            time.sleep(_OPEN_RECOVERY_POLL_S)


def _is_transient_open_connect_error(message: str, *, exc: Exception | None = None) -> bool:
    if _contains_transport_fragment(message) and not _is_transport_error(exc):
        return False
    return any(fragment in message for fragment in _TRANSIENT_CONNECT_ERROR_FRAGMENTS)


def _contains_transport_fragment(message: str) -> bool:
    return any(fragment in message for fragment in _TRANSIENT_CONNECT_ERROR_FRAGMENTS)


def _is_transport_error(exc: Exception | None) -> bool:
    if exc is None:
        return False
    if isinstance(exc, CcbdServiceError):
        return _is_service_transport_message(str(exc))
    cause = getattr(exc, '__cause__', None)
    return isinstance(exc, OSError) or isinstance(cause, OSError)


def _is_service_transport_message(message: str) -> bool:
    return (
        message == 'timed out'
        or message.startswith('[Errno ')
        or message == 'ccbd is unavailable: socket_unreachable'
    )


def _wait_for_attachable_namespace(client) -> dict:
    deadline = time.time() + _OPEN_ATTACH_WAIT_S
    last_transient_error: Exception | None = None
    while True:
        try:
            payload = client.ping('ccbd')
            last_transient_error = None
            tmux_socket_path, tmux_session_name, _workspace_window_name = _attach_payload_fields(payload)
            if tmux_socket_path and tmux_session_name and bool(payload.get('namespace_ui_attachable')):
                return payload
        except Exception as exc:
            if not _is_transient_open_connect_error(str(exc), exc=exc):
                raise
            last_transient_error = exc
        if time.time() >= deadline:
            if last_transient_error is not None:
                raise RuntimeError(
                    f'project namespace is not attachable; run `ccb` first; '
                    f'last transient daemon error: {last_transient_error}'
                ) from last_transient_error
            raise RuntimeError('project namespace is not attachable; run `ccb` first')
        time.sleep(_OPEN_ATTACH_POLL_S)


def _attach_payload_fields(payload: dict) -> tuple[str, str, str]:
    return (
        str(payload.get('namespace_tmux_socket_path') or '').strip(),
        str(payload.get('namespace_tmux_session_name') or '').strip(),
        str(payload.get('namespace_workspace_window_name') or '').strip(),
    )


def _wait_for_tmux_session(tmux_socket_path: str, tmux_session_name: str, *, env: dict[str, str]) -> bool:
    deadline = time.time() + _OPEN_ATTACH_WAIT_S
    while True:
        if _tmux_has_session(tmux_socket_path, tmux_session_name, env=env):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(_OPEN_ATTACH_POLL_S)


def _wait_for_tmux_window(tmux_socket_path: str, target: str, *, env: dict[str, str]) -> bool:
    deadline = time.time() + _OPEN_ATTACH_WAIT_S
    while True:
        if _tmux_select_window(tmux_socket_path, target, env=env):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(_OPEN_ATTACH_POLL_S)


def _attach_tmux_session(tmux_socket_path: str, tmux_session_name: str, *, env: dict[str, str]) -> None:
    deadline = time.time() + _OPEN_ATTACH_WAIT_S
    while True:
        attach = subprocess.run(
            ['tmux', '-S', tmux_socket_path, 'attach-session', '-t', tmux_session_name],
            check=False,
            env=env,
        )
        if attach.returncode == 0:
            return
        if not _tmux_has_session(tmux_socket_path, tmux_session_name, env=env):
            raise RuntimeError('project namespace session exited before attach completed; run `ccb` first')
        if time.time() >= deadline:
            raise RuntimeError('failed to attach project namespace session')
        time.sleep(_OPEN_ATTACH_POLL_S)


def _tmux_has_session(tmux_socket_path: str, tmux_session_name: str, *, env: dict[str, str]) -> bool:
    probe = subprocess.run(
        ['tmux', '-S', tmux_socket_path, 'has-session', '-t', tmux_session_name],
        check=False,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return probe.returncode == 0


def _tmux_select_window(tmux_socket_path: str, target: str, *, env: dict[str, str]) -> bool:
    probe = subprocess.run(
        ['tmux', '-S', tmux_socket_path, 'select-window', '-t', target],
        check=False,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return probe.returncode == 0


__all__ = ['OpenSummary', 'open_project']
