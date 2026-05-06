from __future__ import annotations

from dataclasses import dataclass, replace
import os
import time

from ccbd.socket_client import CcbdClientError


START_CLIENT_TIMEOUT_S = 30.0
START_CLIENT_RETRY_POLL_S = 0.1
_START_RPC_TRANSIENT_ERROR_FRAGMENTS = (
    'No such file or directory',
    'Connection reset by peer',
    'Resource temporarily unavailable',
    'socket_unreachable',
    'timed out',
)


@dataclass(frozen=True)
class StartSummary:
    project_root: str
    project_id: str
    started: tuple[str, ...]
    daemon_started: bool
    socket_path: str
    cleanup_summaries: tuple[object, ...] = ()
    worktree_warnings: tuple[object, ...] = ()
    worktree_retired: tuple[object, ...] = ()


def start_agents(
    context,
    command,
    *,
    ensure_daemon_started_fn,
    startup_report_store_cls,
    cleanup_summary_cls,
    before_client_start_fn=None,
    enrich_summary_fn=None,
) -> StartSummary:
    pre_start_result = before_client_start_fn(context) if before_client_start_fn is not None else None
    handle = ensure_daemon_started_fn(context)
    assert handle.client is not None
    payload = _call_start_with_transient_retries(
        handle.client,
        agent_names=command.agent_names,
        restore=command.restore,
        auto_permission=command.auto_permission,
    )
    _record_daemon_started_flag(
        context,
        daemon_started=handle.started,
        startup_report_store_cls=startup_report_store_cls,
    )
    summary = _summary_from_start_payload(
        context,
        payload,
        daemon_started=handle.started,
        cleanup_summary_cls=cleanup_summary_cls,
    )
    if enrich_summary_fn is not None:
        return enrich_summary_fn(context, summary, pre_start_result)
    return summary


def _client_for_start(client, timeout_s: float):
    with_timeout = getattr(client, 'with_timeout', None)
    if not callable(with_timeout):
        return client
    return with_timeout(timeout_s)


def _start_client_timeout_s() -> float:
    raw = os.environ.get('CCB_CCBD_START_CLIENT_TIMEOUT_S')
    if raw:
        try:
            return max(0.1, float(raw))
        except Exception:
            pass
    return START_CLIENT_TIMEOUT_S


def _call_start_with_transient_retries(client, **kwargs) -> dict:
    timeout_s = _start_client_timeout_s()
    deadline = time.time() + timeout_s
    while True:
        remaining_s = deadline - time.time()
        if remaining_s <= 0:
            raise CcbdClientError('ccbd start RPC timed out')
        try:
            return _client_for_start(client, max(0.1, remaining_s)).start(**kwargs)
        except CcbdClientError as exc:
            remaining_s = deadline - time.time()
            if not _is_transient_start_rpc_error(exc) or remaining_s <= 0:
                raise
            time.sleep(min(START_CLIENT_RETRY_POLL_S, remaining_s))


def _is_transient_start_rpc_error(exc: CcbdClientError) -> bool:
    if not isinstance(exc.__cause__, OSError):
        return False
    message = str(exc)
    return any(fragment in message for fragment in _START_RPC_TRANSIENT_ERROR_FRAGMENTS)


def _summary_from_start_payload(context, payload: dict, *, daemon_started: bool, cleanup_summary_cls) -> StartSummary:
    return StartSummary(
        project_root=str(payload.get("project_root") or context.project.project_root),
        project_id=str(payload.get("project_id") or context.project.project_id),
        started=_started_agents(payload),
        daemon_started=daemon_started,
        socket_path=str(payload.get("socket_path") or context.paths.ccbd_socket_path),
        cleanup_summaries=_cleanup_summaries(payload, cleanup_summary_cls=cleanup_summary_cls),
    )


def _started_agents(payload: dict) -> tuple[str, ...]:
    return tuple(
        str(item).strip()
        for item in (payload.get("started") or ())
        if str(item).strip()
    )


def _cleanup_summaries(payload: dict, *, cleanup_summary_cls) -> tuple[object, ...]:
    return tuple(
        cleanup_summary_cls(
            socket_name=item.get("socket_name"),
            owned_panes=tuple(item.get("owned_panes") or ()),
            active_panes=tuple(item.get("active_panes") or ()),
            orphaned_panes=tuple(item.get("orphaned_panes") or ()),
            killed_panes=tuple(item.get("killed_panes") or ()),
        )
        for item in (payload.get("cleanup_summaries") or ())
        if isinstance(item, dict)
    )


def _record_daemon_started_flag(context, *, daemon_started: bool, startup_report_store_cls) -> None:
    store = startup_report_store_cls(context.paths)
    try:
        report = store.load()
        if report is None:
            return
        store.save(replace(report, daemon_started=daemon_started))
    except Exception:
        return


__all__ = ["START_CLIENT_RETRY_POLL_S", "START_CLIENT_TIMEOUT_S", "StartSummary", "start_agents"]
