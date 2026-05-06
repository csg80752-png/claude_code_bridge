from __future__ import annotations

import time

from ccbd.models import LeaseHealth

from .models import CcbdServiceError, DaemonHandle
from .lifecycle_start import DaemonStartState, finalize_daemon_start, poll_daemon_start_iteration

CONNECT_MOUNTED_READY_WAIT_S = 10.0
CONNECT_MOUNTED_READY_POLL_S = 0.05


def ensure_daemon_started(
    context,
    *,
    clear_shutdown_intent_fn,
    ensure_keeper_started_fn,
    inspect_daemon_fn,
    connect_compatible_daemon_fn,
    should_restart_unreachable_daemon_fn,
    restart_unreachable_daemon_fn,
    spawn_ccbd_process_fn,
    incompatible_daemon_error_fn,
    start_timeout_s: float,
) -> DaemonHandle:
    clear_shutdown_intent_fn(context)
    state = DaemonStartState(keeper_started=bool(ensure_keeper_started_fn(context)))
    deadline = time.time() + start_timeout_s

    while time.time() < deadline:
        handle = poll_daemon_start_iteration(
            context,
            state=state,
            ensure_keeper_started_fn=ensure_keeper_started_fn,
            inspect_daemon_fn=inspect_daemon_fn,
            connect_compatible_daemon_fn=connect_compatible_daemon_fn,
            should_restart_unreachable_daemon_fn=should_restart_unreachable_daemon_fn,
            restart_unreachable_daemon_fn=restart_unreachable_daemon_fn,
            spawn_ccbd_process_fn=spawn_ccbd_process_fn,
        )
        if handle is not None:
            return handle
        time.sleep(0.05)

    return finalize_daemon_start(
        context,
        started=state.started,
        inspect_daemon_fn=inspect_daemon_fn,
        connect_compatible_daemon_fn=connect_compatible_daemon_fn,
        incompatible_daemon_error_fn=incompatible_daemon_error_fn,
    )


def connect_mounted_daemon(
    context,
    *,
    allow_restart_stale: bool,
    inspect_daemon_fn,
    connect_compatible_daemon_fn,
    ensure_daemon_started_fn,
    should_restart_unreachable_daemon_fn,
    incompatible_daemon_error_fn,
) -> DaemonHandle:
    _manager, _guard, inspection = inspect_daemon_fn(context)
    handle = connect_compatible_daemon_fn(context, inspection, restart_on_mismatch=allow_restart_stale)
    if handle is not None:
        return handle
    _manager, _guard, inspection = inspect_daemon_fn(context)
    if allow_restart_stale:
        handle, inspection = _wait_for_transient_mount_readiness(
            context,
            inspection=inspection,
            inspect_daemon_fn=inspect_daemon_fn,
            connect_compatible_daemon_fn=connect_compatible_daemon_fn,
        )
        if handle is not None:
            return handle
    if allow_restart_stale and (
        inspection.health in {LeaseHealth.MISSING, LeaseHealth.UNMOUNTED, LeaseHealth.STALE}
        or should_restart_unreachable_daemon_fn(inspection)
    ):
        return ensure_daemon_started_fn(context)
    if inspection.health is LeaseHealth.UNMOUNTED:
        raise CcbdServiceError('project ccbd is unmounted; run `ccb` first')
    if inspection.health is LeaseHealth.MISSING:
        raise CcbdServiceError('project ccbd is not mounted; run `ccb` first')
    if inspection.socket_connectable:
        handle = connect_compatible_daemon_fn(context, inspection, restart_on_mismatch=False)
        if handle is not None:
            return handle
        raise CcbdServiceError(incompatible_daemon_error_fn())
    raise CcbdServiceError(f'ccbd is unavailable: {inspection.reason}')


def _wait_for_transient_mount_readiness(
    context,
    *,
    inspection,
    inspect_daemon_fn,
    connect_compatible_daemon_fn,
) -> tuple[DaemonHandle | None, object]:
    if not _is_transient_mount_readiness(inspection):
        return None, inspection
    deadline = time.time() + CONNECT_MOUNTED_READY_WAIT_S
    current = inspection
    while time.time() < deadline:
        time.sleep(CONNECT_MOUNTED_READY_POLL_S)
        _manager, _guard, current = inspect_daemon_fn(context)
        handle = connect_compatible_daemon_fn(context, current, restart_on_mismatch=False)
        if handle is not None:
            return handle, current
        if not _is_transient_mount_readiness(current):
            return None, current
    return None, current


def _is_transient_mount_readiness(inspection) -> bool:
    return (
        inspection.health is LeaseHealth.DEGRADED
        and inspection.pid_alive
        and inspection.heartbeat_fresh
        and not inspection.socket_connectable
        and 'socket_unreachable' in str(inspection.reason or '')
    )


__all__ = ['connect_mounted_daemon', 'ensure_daemon_started']
