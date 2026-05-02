from __future__ import annotations

import collections
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import time

from ccbd.system import parse_utc_timestamp
from mailbox_kernel import InboundEventStatus, InboundEventType
from message_bureau.reply_payloads import reply_id_from_payload
from terminal_runtime.tmux_backend_runtime.actions import (
    capture_tmux_value as _capture_tmux_value,
)

from . import cmd_body_store
from .cmd_readiness_probes import (
    READINESS_PROBES as _READINESS_PROBES,
    ReadinessOutcome,
)
from .cmd_delivery_telemetry import (
    record_cmd_delivery_held,
    record_cmd_delivery_success,
    record_header_only_dispatch,
    record_long_reply_fallback,
    record_phase2_failure,
)
from .cmd_transport_planner import plan_cmd_delivery
from .common import head_reply_id, project_id_for_agent
from .preparation_head import resolve_existing_delivery_job
from .preparation_message import build_reply_delivery_job
from .repair import repair_reply_delivery_heads

_logger = logging.getLogger(__name__)

_CMD_PANE_CACHE_TTL = 30.0
_PROBE_LINES = 20
DEFAULT_CMD_SAFE_CONSUMERS = frozenset({'claude'})
# In-memory LRU of reply_ids already injected into the cmd pane. Prevents
# re-injecting the same reply on every tick while the head still waits for
# `client.ack('cmd')` from the cmd user. Bounded so long-lived daemons do
# not leak memory; if a reply ages out of the cache it will be re-injected
# on the next tick, which is visually annoying but never unsafe (cmd text
# inject is an at-least-once delivery, not exactly-once).
_CMD_INJECTED_CACHE_MAX = 256
_CMD_DELIVERED_CACHE_MAX_DISK = 10_000
_CMD_DELIVERED_CACHE_TTL_SECONDS = 48 * 3600
_CMD_DELIVERED_CACHE_FILENAME = 'cmd-delivered-cache.jsonl'


def prepare_reply_deliveries(dispatcher):
    control = getattr(dispatcher, '_message_bureau_control', None)
    bureau = getattr(dispatcher, '_message_bureau', None)
    if control is None or bureau is None:
        return ()

    repair_reply_delivery_heads(dispatcher)
    created = []
    for agent_name in dispatcher._config.agents:
        job = prepare_agent_reply_delivery(dispatcher, agent_name)
        if job is not None:
            created.append(job)

    if bool(getattr(dispatcher._config, 'cmd_enabled', False)):
        _deliver_cmd_replies(dispatcher)

    return tuple(created)


def _deliver_cmd_replies(dispatcher):
    """Side-effect-only cmd delivery: inject pane text, leave head for human ack.

    CCB contract: `client.ack('cmd')` is the human-driven consumer of the cmd
    mailbox head. This function's job is to surface replies in the tmux pane,
    NOT to burn the inbox head. Any claim/consume/abandon here would race
    against the user's ack call (and was the root cause of the reply-loss
    finding from codex structural review 2026-04-22).

    Idempotency: reply_ids already injected are cached in an LRU on the
    dispatcher so we don't spam the pane on every tick while the user
    hasn't acked yet. Environmental failures (no pane, dead backend) return
    silently and let the next tick retry once the environment recovers.
    """
    control = getattr(dispatcher, '_message_bureau_control', None)
    if control is None:
        return

    kernel = getattr(control, '_mailbox_kernel', None)
    if kernel is None:
        return

    head = kernel.head_pending_event('cmd')
    if head is None or head.event_type is not InboundEventType.TASK_REPLY:
        return

    # Only act on fresh heads. If the head is DELIVERING, an older flow did
    # claim it — we leave it for the legacy stale-repair path (or the ack
    # handler) rather than re-acting. CONSUMED/ABANDONED/SUPERSEDED are
    # already filtered out by head_pending_event.
    if head.status not in (InboundEventStatus.CREATED, InboundEventStatus.QUEUED):
        return

    reply_id = reply_id_from_payload(head.payload_ref)
    if not reply_id:
        # Malformed payload. We can't look up the reply, and there's no
        # point re-scanning the same head forever. Leaving it QUEUED would
        # stall the cmd mailbox. This is a true permanent failure — abandon.
        try:
            kernel.abandon('cmd', head.inbound_event_id, finished_at=dispatcher._clock())
        except Exception:
            _logger.debug('cmd head abandon (malformed payload) failed', exc_info=True)
        return

    injected_cache = _get_injected_cache(dispatcher)
    if reply_id in injected_cache:
        injected_cache.move_to_end(reply_id)
        return

    reply_store = getattr(control, '_reply_store', None)
    if reply_store is None:
        return
    reply = reply_store.get_latest(reply_id)
    if reply is None:
        # Rare race with a concurrent reply writer; try again next tick.
        return

    if _should_suppress_cmd_reply(reply):
        _try_ack(kernel, head, timestamp=dispatcher._clock())
        return

    project_root = _resolve_project_root(dispatcher)

    cmd_pane_id = _discover_cmd_pane_id(dispatcher)
    if not cmd_pane_id:
        _logger.debug('cmd pane not discoverable; leaving head queued for next tick')
        return

    backend = _get_tmux_backend(dispatcher)
    if backend is None:
        _logger.debug('cmd tmux backend unavailable; leaving head queued for next tick')
        return

    try:
        if not backend.is_alive(cmd_pane_id):
            _invalidate_cmd_pane_cache(dispatcher)
            _logger.debug('cmd pane %s not alive; leaving head queued for next tick', cmd_pane_id)
            return
    except Exception:
        _invalidate_cmd_pane_cache(dispatcher)
        _logger.debug('cmd pane liveness check raised; leaving head queued', exc_info=True)
        return

    body_char_count = len(reply.reply or '')
    ready, foreground_command, held_reason = _cmd_delivery_gate(
        backend,
        cmd_pane_id,
        project_root=project_root,
    )
    if not ready:
        _hold_cmd_delivery(
            dispatcher,
            reply_id,
            reply=reply,
            project_root=project_root,
            foreground_command=foreground_command,
            body_char_count=body_char_count,
            held_reason=held_reason,
        )
        return

    try:
        plan, fallback = plan_cmd_delivery(
            dispatcher,
            reply,
            project_root=project_root,
            body_store=cmd_body_store,
        )
    except Exception:
        _logger.warning(
            'cmd reply %s planning failed; leaving head queued for next tick',
            reply_id, exc_info=True,
        )
        record_phase2_failure(
            project_root,
            reply_id=reply.reply_id,
            stage='plan',
            reason='exception',
            body_char_count=body_char_count,
            failed_at=dispatcher._clock(),
        )
        return

    if fallback is not None:
        record_long_reply_fallback(
            project_root,
            reply_id=reply.reply_id,
            reason=fallback.reason,
            body_char_count=fallback.body_char_count,
            dispatched_at=dispatcher._clock(),
        )

    ready, foreground_command, held_reason = _cmd_delivery_gate(
        backend,
        cmd_pane_id,
        project_root=project_root,
    )
    if not ready:
        _hold_cmd_delivery(
            dispatcher,
            reply_id,
            reply=reply,
            project_root=project_root,
            foreground_command=foreground_command,
            body_char_count=body_char_count,
            held_reason=held_reason,
        )
        return

    try:
        backend.send_text_to_pane(cmd_pane_id, plan.body)
    except Exception:
        _logger.warning(
            'cmd reply %s pane injection failed; leaving head queued for next tick',
            reply_id, exc_info=True,
        )
        _invalidate_cmd_pane_cache(dispatcher)
        record_phase2_failure(
            project_root,
            reply_id=reply.reply_id,
            stage='send',
            reason='exception',
            body_char_count=body_char_count,
            failed_at=dispatcher._clock(),
        )
        return

    if plan.header_only and plan.body_file is not None and project_root is not None:
        record_header_only_dispatch(
            project_root,
            reply_id=reply.reply_id,
            body_file=plan.body_file,
            dispatched_at=dispatcher._clock(),
            body_char_count=body_char_count,
        )

    record_cmd_delivery_success(
        project_root,
        reply_id=reply.reply_id,
        foreground_command=foreground_command,
        delivered_at=dispatcher._clock(),
        body_char_count=body_char_count,
    )

    # Mark as injected so subsequent ticks don't re-inject. Added AFTER the
    # inject succeeds so a transient send failure retries on the next tick.
    injected_at = _normalize_cache_timestamp(dispatcher._clock())
    injected_cache[reply_id] = injected_at
    injected_cache.move_to_end(reply_id)
    while len(injected_cache) > _CMD_INJECTED_CACHE_MAX:
        injected_cache.popitem(last=False)
    _persist_injected_reply(dispatcher, reply_id, injected_at)


def _get_injected_cache(dispatcher):
    cache = getattr(dispatcher, '_cmd_injected_replies', None)
    if cache is None:
        cache = _load_injected_cache(dispatcher)
        try:
            dispatcher._cmd_injected_replies = cache
        except AttributeError:
            # Attribute assignment failed (e.g., dispatcher uses __slots__)
            # — fall back to the loaded cache for this call only; future
            # calls may reload from disk and re-inject more often.
            return cache
    return cache


def _load_injected_cache(dispatcher):
    cache = collections.OrderedDict()
    cache_path = _cmd_delivered_cache_path(_resolve_project_root(dispatcher))
    if cache_path is None or not cache_path.exists():
        return cache

    cutoff = _cache_reference_time(dispatcher) - timedelta(seconds=_CMD_DELIVERED_CACHE_TTL_SECONDS)
    line_count = 0
    saw_expired = False

    try:
        with cache_path.open('r', encoding='utf-8') as handle:
            for raw_line in handle:
                line_count += 1
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    _logger.warning('cmd injected cache line decode failed', exc_info=True)
                    continue

                reply_id = str(payload.get('reply_id', '') or '').strip()
                injected_at = str(payload.get('injected_at', '') or '').strip()
                if not reply_id or not injected_at:
                    _logger.debug('cmd injected cache record missing fields: %r', payload)
                    continue
                try:
                    injected_dt = _parse_cache_timestamp(injected_at)
                except Exception:
                    _logger.debug('cmd injected cache timestamp parse failed', exc_info=True)
                    continue
                if injected_dt < cutoff:
                    saw_expired = True
                    continue

                if reply_id in cache:
                    cache.pop(reply_id, None)
                cache[reply_id] = injected_dt.isoformat().replace('+00:00', 'Z')
                while len(cache) > _CMD_DELIVERED_CACHE_MAX_DISK:
                    cache.popitem(last=False)
    except Exception:
        _logger.warning('cmd injected cache load failed', exc_info=True)
        return cache

    if line_count > _CMD_DELIVERED_CACHE_MAX_DISK or saw_expired:
        _compact_injected_cache_file(cache_path, cache)
    return cache


def _try_ack(kernel, head, *, timestamp: str | None = None) -> bool:
    try:
        acked = kernel.ack_reply(
            'cmd',
            head.inbound_event_id,
            started_at=timestamp,
            finished_at=timestamp,
        )
    except Exception:
        _logger.debug('cmd suppressed-reply ack failed', exc_info=True)
        return False
    return bool(
        acked is not None
        and getattr(acked, 'inbound_event_id', None) == head.inbound_event_id
        and getattr(acked, 'status', None) is InboundEventStatus.CONSUMED
    )


def _should_suppress_cmd_reply(reply) -> bool:
    if _is_heartbeat_notice(reply):
        return True
    if str(getattr(reply, 'reply', '') or '').strip():
        return False
    terminal_status = getattr(getattr(reply, 'terminal_status', None), 'value', None)
    return str(terminal_status or '').strip() == 'cancelled'


def _is_heartbeat_notice(reply) -> bool:
    diagnostics = getattr(reply, 'diagnostics', None)
    if not isinstance(diagnostics, dict):
        return False
    return (
        diagnostics.get('notice') is True
        and str(diagnostics.get('notice_kind') or '').strip() == 'heartbeat'
    )


def _hold_cmd_delivery(
    dispatcher,
    reply_id: str,
    *,
    reply,
    project_root: Path | None,
    foreground_command: str,
    body_char_count: int,
    held_reason: str,
) -> None:
    held_key = (reply_id, held_reason, foreground_command)
    held_cache = getattr(dispatcher, '_cmd_held_replies', None)
    if held_cache is None:
        held_cache = set()
        try:
            dispatcher._cmd_held_replies = held_cache
        except AttributeError:
            held_cache = set()
    if held_key in held_cache:
        return
    held_cache.add(held_key)
    record_cmd_delivery_held(
        project_root,
        reply_id=reply.reply_id,
        foreground_command=foreground_command,
        held_at=dispatcher._clock(),
        body_char_count=body_char_count,
        held_reason=held_reason,
    )


def _cmd_delivery_gate(
    backend,
    pane_id: str,
    *,
    project_root: Path | None,
) -> tuple[bool, str, str]:
    foreground_command = _cmd_pane_foreground_command(backend, pane_id)
    safe_consumers = _load_cmd_safe_consumers(project_root)
    if foreground_command not in safe_consumers:
        return False, foreground_command, 'not_safe_consumer'
    readiness = _cmd_pane_readiness(backend, pane_id, foreground_command)
    if readiness is not ReadinessOutcome.READY:
        return False, foreground_command, readiness.value
    return True, foreground_command, ''


def _cmd_pane_foreground_command(backend, pane_id: str) -> str:
    pane_id = str(pane_id or '').strip()
    if backend is None or not pane_id:
        return ''
    try:
        return str(
            _capture_tmux_value(
                backend,
                pane_id,
                "#{pane_current_command}",
                timeout=1.0,
            ) or ''
        ).strip()
    except Exception:
        return ''


def _cmd_pane_readiness(
    backend,
    pane_id: str,
    foreground_command: str,
) -> ReadinessOutcome:
    probe = _READINESS_PROBES.get(foreground_command)
    if probe is None:
        return ReadinessOutcome.UNKNOWN_CONSUMER
    if os.environ.get('CCB_CMD_READY_GATE', '1') == '0':
        return ReadinessOutcome.READY
    get_pane_content = getattr(backend, 'get_pane_content', None)
    if not callable(get_pane_content):
        return ReadinessOutcome.PROBE_UNAVAILABLE
    try:
        text = str(get_pane_content(pane_id, lines=_PROBE_LINES) or '')
    except Exception:
        return ReadinessOutcome.PROBE_UNAVAILABLE
    if not probe(text):
        return ReadinessOutcome.NOT_READY
    try:
        stable_text = str(get_pane_content(pane_id, lines=_PROBE_LINES) or '')
    except Exception:
        return ReadinessOutcome.PROBE_UNAVAILABLE
    if stable_text != text:
        return ReadinessOutcome.NOT_READY
    return ReadinessOutcome.READY if probe(stable_text) else ReadinessOutcome.NOT_READY


def _load_cmd_safe_consumers(project_root: Path | None) -> frozenset[str]:
    env_val = str(os.environ.get('CCB_CMD_SAFE_CONSUMERS', '') or '').strip()
    if env_val:
        return frozenset(name.strip() for name in env_val.split(',') if name.strip())
    if project_root is not None:
        config_file = Path(project_root) / '.ccb' / 'cmd-safe-consumers.txt'
        if config_file.exists():
            try:
                consumers = []
                for line in config_file.read_text(encoding='utf-8').splitlines():
                    stripped = line.strip()
                    if stripped and not stripped.startswith('#'):
                        consumers.append(stripped)
                if consumers:
                    return frozenset(consumers)
            except OSError:
                _logger.debug('failed to read cmd safe-consumer allowlist: %s', config_file, exc_info=True)
    return DEFAULT_CMD_SAFE_CONSUMERS


def _persist_injected_reply(dispatcher, reply_id: str, injected_at: str) -> None:
    cache_path = _cmd_delivered_cache_path(_resolve_project_root(dispatcher))
    if cache_path is None:
        return

    record = json.dumps(
        {
            'reply_id': str(reply_id or '').strip(),
            'injected_at': _normalize_cache_timestamp(injected_at),
        },
        separators=(',', ':'),
    )
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, 'a', encoding='utf-8') as handle:
            handle.write(record)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        _logger.debug('cmd injected cache persist failed', exc_info=True)


def _compact_injected_cache_file(path: Path, cache) -> None:
    tmp_path = path.with_name(f'{path.name}.tmp.{os.getpid()}')
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tmp_path.open('w', encoding='utf-8') as handle:
            for reply_id, injected_at in cache.items():
                handle.write(
                    json.dumps(
                        {'reply_id': reply_id, 'injected_at': injected_at},
                        separators=(',', ':'),
                    )
                )
                handle.write('\n')
        os.replace(tmp_path, path)
    except Exception:
        _logger.warning('cmd injected cache compaction failed', exc_info=True)
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass


def _cmd_delivered_cache_path(project_root) -> Path | None:
    if project_root is None:
        return None
    return Path(project_root) / '.ccb' / 'ccbd' / _CMD_DELIVERED_CACHE_FILENAME


def _cache_reference_time(dispatcher) -> datetime:
    clock = getattr(dispatcher, '_clock', None)
    if callable(clock):
        try:
            return _parse_cache_timestamp(clock())
        except Exception:
            _logger.debug('cmd injected cache clock parse failed', exc_info=True)
    return datetime.now(timezone.utc)


def _normalize_cache_timestamp(value: str) -> str:
    try:
        return _parse_cache_timestamp(value).isoformat().replace('+00:00', 'Z')
    except Exception:
        return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _parse_cache_timestamp(value: str) -> datetime:
    parsed = parse_utc_timestamp(str(value or '').strip())
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# Fix #3: TTL-based cache instead of permanent.
def _discover_cmd_pane_id(dispatcher) -> str | None:
    cache = getattr(dispatcher, '_cmd_pane_cache', None)
    now = time.monotonic()

    if cache is not None:
        cached_id, cached_at = cache
        if (now - cached_at) < _CMD_PANE_CACHE_TTL:
            return cached_id if cached_id else None

    layout = getattr(dispatcher, '_layout', None)
    if layout is None:
        return None

    pane_id = _lookup_cmd_pane_id(dispatcher, layout)

    if pane_id is not None:
        try:
            dispatcher._cmd_pane_cache = (pane_id, now)
        except AttributeError:
            pass

    return pane_id


def _invalidate_cmd_pane_cache(dispatcher):
    try:
        dispatcher._cmd_pane_cache = None
    except AttributeError:
        pass


def _resolve_project_root(dispatcher):
    layout = getattr(dispatcher, '_layout', None)
    if layout is None:
        return None
    root = getattr(layout, 'project_root', None)
    return root


def _resolve_project_id(dispatcher, layout) -> str | None:
    runtime_service = getattr(dispatcher, '_runtime_service', None)
    project_id = str(getattr(runtime_service, '_project_id', '') or '').strip()
    if project_id:
        return project_id
    try:
        from project.ids import compute_project_id as _compute_project_id

        return _compute_project_id(layout.project_root)
    except Exception:
        pass
    try:
        from ccbd.keeper_runtime.state import compute_project_id as _compute_project_id

        return _compute_project_id(layout.project_root)
    except Exception:
        return None


def _lookup_cmd_pane_id(dispatcher, layout) -> str | None:
    try:
        from ccbd.services.project_namespace import ProjectNamespaceController
        from ccbd.services.project_namespace_runtime.backend import build_backend

        project_id = _resolve_project_id(dispatcher, layout)
        if not project_id:
            return None
        controller = ProjectNamespaceController(layout, project_id)
        namespace = controller.load()
        if namespace is None:
            return None

        socket_path = str(getattr(namespace, 'tmux_socket_path', None) or '').strip()
        if not socket_path:
            return None

        backend = build_backend(controller._backend_factory, socket_path=socket_path)
        project_id_str = str(project_id)

        runner = getattr(backend, '_tmux_run', None)
        if not callable(runner):
            return None

        try:
            cp = runner(
                ['list-panes', '-a', '-F',
                 '#{pane_id}\t#{@ccb_role}\t#{@ccb_slot}\t#{@ccb_project_id}'],
                capture=True,
                check=True,
            )
        except Exception:
            return None

        stdout = getattr(cp, 'stdout', '') or ''
        for line in stdout.splitlines():
            parts = line.strip().split('\t')
            if len(parts) < 4:
                continue
            pane_id, role, slot, pane_project = parts[0], parts[1], parts[2], parts[3]
            if (role == 'cmd' and slot == 'cmd'
                    and pane_project == project_id_str
                    and pane_id.startswith('%')):
                return pane_id
    except Exception:
        _logger.debug('cmd pane discovery failed', exc_info=True)

    return None


def _get_tmux_backend(dispatcher):
    try:
        from ccbd.services.project_namespace import ProjectNamespaceController
        from ccbd.services.project_namespace_runtime.backend import build_backend

        layout = dispatcher._layout
        project_id = _resolve_project_id(dispatcher, layout)
        if not project_id:
            return None
        controller = ProjectNamespaceController(layout, project_id)
        namespace = controller.load()
        if namespace is None:
            return None
        socket_path = str(getattr(namespace, 'tmux_socket_path', None) or '').strip()
        if not socket_path:
            return None
        return build_backend(controller._backend_factory, socket_path=socket_path)
    except Exception:
        _logger.debug('tmux backend construction failed', exc_info=True)
        return None


def prepare_agent_reply_delivery(dispatcher, agent_name: str):
    from .common import head_reply_event

    head = head_reply_event(dispatcher, agent_name)
    if head is None:
        return None
    reply_id = head_reply_id(head)
    if not reply_id:
        return None

    head = resolve_existing_delivery_job(
        dispatcher,
        agent_name,
        head,
        reply_id=reply_id,
    )
    if head is None or head is False:
        return None

    reply = dispatcher._message_bureau_control._reply_store.get_latest(reply_id)
    if reply is None:
        return None

    accepted_at = dispatcher._clock()
    project_id = project_id_for_agent(dispatcher, agent_name)
    if not project_id:
        return None
    return build_reply_delivery_job(
        dispatcher,
        agent_name=agent_name,
        head=head,
        reply=reply,
        accepted_at=accepted_at,
        project_id=project_id,
    )


__all__ = ['prepare_reply_deliveries']
