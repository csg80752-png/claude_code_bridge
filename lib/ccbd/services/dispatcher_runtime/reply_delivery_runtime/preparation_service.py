from __future__ import annotations

import collections
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import time

from mailbox_kernel.gc import compact_mailbox_jsonl
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
    record_cmd_delivery_header_inject_error,
    record_cmd_delivery_header_inject_success,
    record_cmd_delivery_held,
    record_cmd_delivery_success,
    record_long_reply_fallback,
    record_phase2_failure,
)
from .cmd_transport_planner import CmdDeliveryMode, effective_cmd_delivery_mode
from .cmd_transport_planner import plan_cmd_delivery, prepare_cmd_payload as _prepare_cmd_payload
from .cmd_transport_planner import resolve_cmd_delivery_mode
from .common import head_reply_id, project_id_for_agent
from .preparation_head import resolve_existing_delivery_job
from .preparation_message import build_reply_delivery_job
from .repair import repair_reply_delivery_heads

_logger = logging.getLogger(__name__)

# v8.4 PR 7 (Shape A 2026-05-05): the cross-sweep TTL cache for cmd pane id
# was removed because it survived daemon-alive pane replacement (e.g., the
# supervisor overwriting bootstrap_cmd_pane), masking the new live pane for
# up to TTL seconds and queueing stale-pane sends that fail with "target
# pane has exited". The constant is preserved as a tombstone reference in
# case external diagnostics still grep it; production code no longer
# consults a cross-sweep cache.
_CMD_PANE_CACHE_TTL_DEPRECATED = 0.0
_PROBE_LINES = 20
DEFAULT_CMD_SAFE_CONSUMERS = frozenset({'claude'})

# v8.4 PR 7 (Shape B 2026-05-05): K consecutive sweep stops on the same
# inbound event before phase-2 abandon. Bounds runaway retries when the
# cmd pane is permanently dead (e.g., user closed terminal entirely). Env
# var override is read at call time so live tests / canaries can rotate
# the threshold without restart.
_CMD_PANE_RETRY_DEFAULT = 3
_CMD_PANE_RETRY_ENV = 'CCB_CMD_REPLY_MAX_RETRIES'

# v8.4 PR #2 (diag round) — non-behavioral. Spec: docs/v8.4-plan.md lines
# 128-136. One-shot per ccbd lifetime, gated on pending_cmd_replies > 0:
# the first tick where cmd has unflushed task_reply events logs the
# dispatcher's cmd pane view (cached id, fresh tmux discovery, is_alive)
# plus the on-disk startup-report's bootstrap_cmd_pane (which may be
# supervisor-overwritten — see project_startup_report_overwrite_supervisor_vs_app).
# The pending>0 gate is required: _deliver_cmd_replies runs every tick
# regardless of traffic, so an unconditional emit would burn the one-shot
# at startup tick=0 and lose the wedge evidence we actually need (codex R1).
# The Issue #3 fix PR (v8.4-cmd) consumes these warnings to lock the fix
# shape (re-resolve via __ccb_ctl pane title vs invalidate-on-stale).
_cmd_pane_diag_emitted: bool = False
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
_MAILBOX_GC_INTERVAL_SECONDS = 3600.0


def prepare_reply_deliveries(dispatcher):
    control = getattr(dispatcher, '_message_bureau_control', None)
    bureau = getattr(dispatcher, '_message_bureau', None)
    if control is None or bureau is None:
        return ()

    _maybe_compact_mailboxes(dispatcher)
    repair_reply_delivery_heads(dispatcher)
    created = []
    for agent_name in dispatcher._config.agents:
        job = prepare_agent_reply_delivery(dispatcher, agent_name)
        if job is not None:
            created.append(job)

    if bool(getattr(dispatcher._config, 'cmd_enabled', False)):
        _deliver_cmd_replies(dispatcher)

    return tuple(created)


def _maybe_compact_mailboxes(dispatcher) -> None:
    now = time.monotonic()
    last_run = getattr(dispatcher, '_mailbox_gc_last_run_monotonic', None)
    if last_run is not None and (now - float(last_run)) < _MAILBOX_GC_INTERVAL_SECONDS:
        return
    try:
        dispatcher._mailbox_gc_last_run_monotonic = now
    except AttributeError:
        pass

    control = getattr(dispatcher, '_message_bureau_control', None)
    kernel = getattr(control, '_mailbox_kernel', None)
    layout = getattr(dispatcher, '_layout', None)
    config = getattr(dispatcher, '_config', None)
    if kernel is None or layout is None or config is None:
        return

    agent_names = tuple(dict.fromkeys(('cmd', *tuple(getattr(config, 'agents', ()) or ()))))
    cache_owner = getattr(getattr(kernel, '_inbound_store', None), '_store', None)
    reply_cache_owner = getattr(getattr(control, '_reply_store', None), '_store', None)
    try:
        compact_mailbox_jsonl(
            layout,
            agent_names=agent_names,
            now=datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            cache_owner=cache_owner,
            reply_cache_owner=reply_cache_owner,
        )
    except Exception:
        _logger.debug('mailbox jsonl gc failed', exc_info=True)


def _deliver_cmd_replies(dispatcher):
    pending_count = _count_pending_cmd_replies(dispatcher)
    if pending_count > 0:
        _maybe_emit_cmd_pane_discovery_diag(dispatcher, pending_count=pending_count)
    return _deliver_cmd_replies_impl(dispatcher)


def _deliver_cmd_replies_impl(dispatcher):
    """Side-effect-only cmd delivery: inject pane text, leave head for human ack.

    CCB contract: `client.ack('cmd')` is the human-driven consumer of the cmd
    mailbox head. This function's job is to surface replies in the tmux pane,
    NOT to burn the inbox head. Any claim/consume/abandon here would race
    against the user's ack call (and was the root cause of the reply-loss
    finding from codex structural review 2026-04-22).

    Per-tick sweep: process ALL pending non-terminal task_reply events on
    the cmd queue, not just the head. The injected_cache acts as the
    idempotent guard so events injected on a previous tick are not re-sent.
    Without this sweep, a pinned-head real reply (still awaiting human ack)
    would block subsequent replies indefinitely, even though they are
    independently deliverable to the pane (root cause confirmed 2026-05-03
    KST via live ack probe; v8.3.3 cmd-pending-sweep fix).

    Ordering preservation: the sweep ONLY skips ahead past events that are
    fully resolved this tick — already-injected (cache hit), abandoned
    (malformed payload OR planning exception), or suppressed-and-acked
    (heartbeat / cancelled empty). Any TRANSIENT deferred outcome (gate
    hold, reply_store race, pane unavailability) STOPS the sweep, so a
    later reply never overtakes an earlier reply still waiting to surface.
    Without this stop-on-defer rule, a transient pre-plan gate `not ready`
    on r1 would let r2 inject ahead of r1, violating cmd mailbox ordering
    (codex review [P2] 2026-05-03 KST; v8.3.3 R2 fix).

    Planning exceptions are TERMINAL not deferred: the event is abandoned
    via `kernel.abandon` and the sweep continues. Deterministic plan
    failures (e.g., body over MAX_CMD_HEADER_BYTES_FIELD) would otherwise
    retry-and-block forever if the sweep stopped on them, wedging every
    later cmd reply behind the bad event (codex review [P2] 2026-05-03
    KST; v8.3.3 R3 fix). The phase-2 failure record is still emitted for
    telemetry / triage so the abandoned reply is not silently lost.

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

    pending = kernel.pending_events('cmd', event_type=InboundEventType.TASK_REPLY)
    if not pending:
        return

    reply_store = getattr(control, '_reply_store', None)
    if reply_store is None:
        return

    injected_cache = _get_injected_cache(dispatcher)
    project_root = _resolve_project_root(dispatcher)
    delivery_mode_result = _get_cmd_delivery_mode_result(dispatcher, project_root)
    _prune_pane_retry_counts(dispatcher, pending)

    # Lazy pane/backend discovery: suppressed replies (heartbeats / cancelled
    # empty) auto-ack via `_try_ack` and never touch the pane, so we only
    # probe pane state when an actual inject is needed. `pane_state[0]` is
    # the pane id, `pane_state[1]` is the backend, `pane_state[2]` flags
    # known-bad env so subsequent inject events skip without re-probing.
    pane_state: list = [None, None, False]

    def _resolve_live_pane() -> tuple[str | None, object | None]:
        """Single fresh pane resolution + liveness check. Returns
        (pane_id, backend) on success or (None, None) on any failure.
        Used both by `_ensure_pane_init` (initial resolution) and by the
        send-fail retry path (Shape B)."""
        candidate_pane_id = _discover_cmd_pane_id(dispatcher)
        if not candidate_pane_id:
            return None, None
        candidate_backend = _get_tmux_backend(dispatcher)
        if candidate_backend is None:
            return None, None
        try:
            alive = candidate_backend.is_alive(candidate_pane_id)
        except Exception:
            _logger.debug(
                'cmd pane %s liveness check raised', candidate_pane_id, exc_info=True,
            )
            return None, None
        if not alive:
            return None, None
        return candidate_pane_id, candidate_backend

    def _ensure_pane_init() -> bool:
        # Shape B (retry once): if the first fresh resolution fails, try
        # exactly once more. Daemon-alive pane replacement (supervisor
        # rotates the cmd window mid-tick) lands a brand-new pane id in
        # tmux metadata; one retry typically picks it up without dropping
        # the sweep. After the retry, give up for this tick — repeated
        # failures advance the per-event K counter (handled below).
        if pane_state[2]:
            return False
        if pane_state[0] is not None and pane_state[1] is not None:
            return True
        pane_id, backend = _resolve_live_pane()
        if pane_id is None:
            pane_id, backend = _resolve_live_pane()
        if pane_id is None:
            _logger.debug(
                'cmd pane unavailable after fresh resolve + retry; deferring sweep',
            )
            pane_state[2] = True
            return False
        pane_state[0] = pane_id
        pane_state[1] = backend
        return True

    max_retries = _max_cmd_pane_retries()

    def _record_sweep_stop_and_maybe_abandon(
        head,
        reply,
        *,
        body_char_count: int,
        foreground_command: str,
        pane_alive: bool,
    ) -> None:
        """Bump the K-counter for this inbound event after a pane-related
        sweep stop. If the count reaches `max_retries`, abandon the event
        with a phase-2 `retry_exhausted` failure so it cannot block the
        cmd queue indefinitely. The caller still `break`s after this; the
        next tick re-enters with the abandoned event filtered out by
        `pending_events`.
        """
        new_count = _bump_pane_retry_count(dispatcher, head.inbound_event_id)
        if new_count < max_retries:
            return
        try:
            kernel.abandon('cmd', head.inbound_event_id, finished_at=dispatcher._clock())
        except Exception:
            _logger.debug(
                'cmd event abandon (retry exhausted) failed', exc_info=True,
            )
            return
        record_phase2_failure(
            project_root,
            reply_id=getattr(reply, 'reply_id', '') or '',
            stage='abandon',
            reason='retry_exhausted',
            body_char_count=body_char_count,
            failed_at=dispatcher._clock(),
            foreground_command=foreground_command,
            pane_alive=pane_alive,
            cached=False,
        )
        _logger.warning(
            'cmd reply %s abandoned after %d consecutive sweep stops; pane unrecoverable',
            getattr(reply, 'reply_id', '?'),
            new_count,
        )
        _clear_pane_retry_count(dispatcher, head.inbound_event_id)

    def _pane_dead_after_gate_hold(backend, pane_id: str) -> bool:
        try:
            return not bool(backend.is_alive(pane_id))
        except Exception:
            _logger.debug(
                'cmd pane %s liveness recheck after gate hold raised',
                pane_id, exc_info=True,
            )
            return True

    for head in pending:
        # Only act on fresh events. DELIVERING means an older flow did claim
        # the event — leave it for the legacy stale-repair path (or the ack
        # handler) rather than re-acting. CONSUMED/ABANDONED/SUPERSEDED are
        # already filtered out by pending_events.
        if head.status not in (InboundEventStatus.CREATED, InboundEventStatus.QUEUED):
            _clear_pane_retry_count(dispatcher, head.inbound_event_id)
            continue

        reply_id = reply_id_from_payload(head.payload_ref)
        if not reply_id:
            # Malformed payload. We can't look up the reply, and there's no
            # point re-scanning the same event forever. Leaving it QUEUED
            # would only stall this slot in the cmd mailbox. Permanent
            # failure — abandon and move on to the next pending event.
            _clear_pane_retry_count(dispatcher, head.inbound_event_id)
            try:
                kernel.abandon('cmd', head.inbound_event_id, finished_at=dispatcher._clock())
            except Exception:
                _logger.debug('cmd event abandon (malformed payload) failed', exc_info=True)
            continue

        if reply_id in injected_cache:
            _clear_pane_retry_count(dispatcher, head.inbound_event_id)
            injected_cache.move_to_end(reply_id)
            continue

        reply = reply_store.get_latest(reply_id)
        if reply is None:
            # Rare race with a concurrent reply writer. Stop the sweep here
            # so a later reply that is fully written cannot overtake this
            # one in the cmd pane; retry this event next tick.
            _clear_pane_retry_count(dispatcher, head.inbound_event_id)
            break

        if _should_suppress_cmd_reply(reply):
            _clear_pane_retry_count(dispatcher, head.inbound_event_id)
            _try_ack(kernel, head, timestamp=dispatcher._clock())
            continue

        if not _ensure_pane_init():
            # Pane unavailable after Shape B retry. Bump the K-counter for
            # this head so a permanently dead pane cannot wedge the queue
            # forever; if we have hit max retries, the abandon is recorded
            # before we break. Either way the sweep stops here to preserve
            # ordering for the next tick.
            _record_sweep_stop_and_maybe_abandon(
                head,
                reply,
                body_char_count=len(getattr(reply, 'reply', '') or ''),
                foreground_command='',
                pane_alive=False,
            )
            break
        cmd_pane_id = pane_state[0]
        backend = pane_state[1]

        body_char_count = len(reply.reply or '')
        ready, foreground_command, held_reason = _cmd_delivery_gate(
            backend,
            cmd_pane_id,
            project_root=project_root,
        )
        if not ready:
            if _pane_dead_after_gate_hold(backend, cmd_pane_id):
                _record_sweep_stop_and_maybe_abandon(
                    head,
                    reply,
                    body_char_count=body_char_count,
                    foreground_command=foreground_command,
                    pane_alive=False,
                )
                break
            _hold_cmd_delivery(
                dispatcher,
                reply_id,
                reply=reply,
                project_root=project_root,
                foreground_command=foreground_command,
                body_char_count=body_char_count,
                held_reason=held_reason,
                delivery_mode_result=delivery_mode_result,
            )
            _clear_pane_retry_count(dispatcher, head.inbound_event_id)
            # Stop the sweep on a pre-plan hold of an undelivered reply.
            # Continuing past a held r1 could let r2's gate probe succeed
            # and inject r2 into the pane before r1, breaking cmd mailbox
            # order (codex review [P2] 2026-05-03 KST).
            break

        try:
            plan, fallback = plan_cmd_delivery(
                dispatcher,
                reply,
                project_root=project_root,
                body_store=cmd_body_store,
                delivery_mode_result=delivery_mode_result,
            )
        except Exception:
            _logger.warning(
                'cmd reply %s planning failed; abandoning event to unblock queue',
                reply_id, exc_info=True,
            )
            record_phase2_failure(
                project_root,
                reply_id=reply.reply_id,
                stage='plan',
                reason='exception',
                body_char_count=body_char_count,
                failed_at=dispatcher._clock(),
                foreground_command=foreground_command,
                pane_alive=True,
                cached=False,
            )
            # Deterministic plan failures (e.g., body over MAX_CMD_HEADER_BYTES_FIELD)
            # would retry-and-block forever if we just `break` here, wedging
            # every later cmd reply behind the bad event. Treat planning
            # exceptions as terminal: abandon the event so the queue can
            # progress, then continue to the next pending event. Transient
            # plan failures lose their retry, but record_phase2_failure
            # above keeps them visible for telemetry / triage
            # (codex review [P2] 2026-05-03 KST; v8.3.3 R3 fix).
            abandoned = False
            _clear_pane_retry_count(dispatcher, head.inbound_event_id)
            try:
                kernel.abandon('cmd', head.inbound_event_id, finished_at=dispatcher._clock())
                abandoned = True
            except Exception:
                _logger.debug('cmd event abandon (planning failure) failed', exc_info=True)
            if not abandoned:
                # Abandon I/O failure → event remains non-terminal. Stop the
                # sweep so a later reply does not inject ahead of this still
                # unresolved event (preserves the R2 ordering rule under
                # abandon failure; codex review [P2] 2026-05-03 KST;
                # v8.3.3 R4 fix).
                break
            continue

        if fallback is not None:
            record_long_reply_fallback(
                project_root,
                reply_id=reply.reply_id,
                reason=fallback.reason,
                body_char_count=fallback.body_char_count,
                dispatched_at=dispatcher._clock(),
            )

        # Re-check gate after planning. Race protection: tmux foreground
        # command may have changed between the pre-plan check and now.
        ready, foreground_command, held_reason = _cmd_delivery_gate(
            backend,
            cmd_pane_id,
            project_root=project_root,
        )
        if not ready:
            if _pane_dead_after_gate_hold(backend, cmd_pane_id):
                _record_sweep_stop_and_maybe_abandon(
                    head,
                    reply,
                    body_char_count=body_char_count,
                    foreground_command=foreground_command,
                    pane_alive=False,
                )
                break
            _hold_cmd_delivery(
                dispatcher,
                reply_id,
                reply=reply,
                project_root=project_root,
                foreground_command=foreground_command,
                body_char_count=body_char_count,
                held_reason=held_reason,
                delivery_mode_result=delivery_mode_result,
            )
            _clear_pane_retry_count(dispatcher, head.inbound_event_id)
            # Stop the sweep on a post-plan hold for the same ordering
            # reason as the pre-plan hold above.
            break

        send_succeeded = False
        try:
            backend.send_text_to_pane(cmd_pane_id, plan.body)
            send_succeeded = True
        except Exception:
            _logger.debug(
                'cmd reply %s initial pane injection failed; attempting fresh-resolve retry',
                reply_id, exc_info=True,
            )

        if not send_succeeded:
            # Shape B (2026-05-05) — invalidate within-sweep cache, fresh
            # re-resolve, re-verify gate (foreground + readiness), retry
            # the send exactly once. Daemon-alive pane replacement most
            # often hits this path: the original pane id died mid-sweep
            # and tmux now has a new __ccb_ctl pane id under the same
            # role+slot+project_id triplet. One re-resolve picks it up.
            pane_state[0] = None
            pane_state[1] = None
            retry_pane_id, retry_backend = _resolve_live_pane()
            retry_ready = False
            retry_foreground = foreground_command
            if retry_pane_id is not None and retry_backend is not None:
                retry_ready, retry_foreground, _retry_held_reason = _cmd_delivery_gate(
                    retry_backend,
                    retry_pane_id,
                    project_root=project_root,
                )
            if retry_pane_id is not None and retry_backend is not None and retry_ready:
                try:
                    retry_backend.send_text_to_pane(retry_pane_id, plan.body)
                    send_succeeded = True
                    cmd_pane_id = retry_pane_id
                    backend = retry_backend
                    foreground_command = retry_foreground
                    pane_state[0] = retry_pane_id
                    pane_state[1] = retry_backend
                except Exception:
                    _logger.debug(
                        'cmd reply %s retry pane injection failed', reply_id, exc_info=True,
                    )

        if not send_succeeded:
            _logger.warning(
                'cmd reply %s pane injection failed after retry; leaving event queued for next tick',
                reply_id,
            )
            _invalidate_cmd_pane_cache(dispatcher)
            record_phase2_failure(
                project_root,
                reply_id=reply.reply_id,
                stage='send',
                reason='exception',
                body_char_count=body_char_count,
                failed_at=dispatcher._clock(),
                foreground_command=foreground_command,
                pane_alive=False,
                cached=False,
            )
            if plan.header_only:
                record_cmd_delivery_header_inject_error(
                    project_root,
                    reply_id=reply.reply_id,
                    foreground_command=foreground_command,
                    failed_at=dispatcher._clock(),
                    body_char_count=body_char_count,
                    reason='exception',
                    delivery_mode=delivery_mode_result.mode.value,
                    header_only_compatible=delivery_mode_result.header_only_compatible,
                )
            # Bump K-counter and (if exhausted) abandon the event with a
            # phase-2 failure record so a permanently dead pane cannot
            # wedge the cmd queue forever. Either way break the sweep —
            # subsequent events would hit the same dead pane.
            _record_sweep_stop_and_maybe_abandon(
                head,
                reply,
                body_char_count=body_char_count,
                foreground_command=foreground_command,
                pane_alive=False,
            )
            break

        # Send succeeded (initial or retry). Clear any stale K-counter
        # accumulated by prior sweep stops on this same inbound event.
        _clear_pane_retry_count(dispatcher, head.inbound_event_id)

        delivered_at = dispatcher._clock()
        if plan.header_only:
            record_cmd_delivery_header_inject_success(
                project_root,
                reply_id=reply.reply_id,
                foreground_command=foreground_command,
                delivered_at=delivered_at,
                body_char_count=body_char_count,
                delivery_mode=delivery_mode_result.mode.value,
                header_only_compatible=delivery_mode_result.header_only_compatible,
            )
        else:
            record_cmd_delivery_success(
                project_root,
                reply_id=reply.reply_id,
                foreground_command=foreground_command,
                delivered_at=delivered_at,
                body_char_count=body_char_count,
                delivery_mode=delivery_mode_result.mode.value,
                header_only_compatible=delivery_mode_result.header_only_compatible,
            )

        # Mark as injected so subsequent ticks don't re-inject. Added AFTER
        # the inject succeeds so a transient send failure retries on the
        # next tick.
        if _should_cache_cmd_delivery(plan, delivery_mode_result):
            injected_at = _normalize_cache_timestamp(dispatcher._clock())
            injected_cache[reply_id] = injected_at
            injected_cache.move_to_end(reply_id)
            while len(injected_cache) > _CMD_INJECTED_CACHE_MAX:
                injected_cache.popitem(last=False)
            _persist_injected_reply(dispatcher, reply_id, injected_at)


def _get_cmd_delivery_mode_result(dispatcher, project_root):
    result = getattr(dispatcher, '_cmd_delivery_mode_result', None)
    if result is not None:
        return result
    result = resolve_cmd_delivery_mode(project_root=project_root)
    try:
        dispatcher._cmd_delivery_mode_result = result
    except AttributeError:
        pass
    return result


def _should_cache_cmd_delivery(plan, delivery_mode_result) -> bool:
    if delivery_mode_result.mode is CmdDeliveryMode.HEADER_ONLY:
        return (
            bool(plan.header_only)
            and delivery_mode_result.header_only_compatible
            and effective_cmd_delivery_mode(delivery_mode_result) is CmdDeliveryMode.HEADER_ONLY
        )
    return True


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
    delivery_mode_result=None,
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
        delivery_mode=getattr(getattr(delivery_mode_result, 'mode', None), 'value', 'full_body'),
        header_only_compatible=bool(getattr(delivery_mode_result, 'header_only_compatible', False)),
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
    signal_outcome = _cmd_pane_serialized_readiness_signal(backend, pane_id, foreground_command)
    if signal_outcome is ReadinessOutcome.READY:
        return ReadinessOutcome.READY
    if signal_outcome is ReadinessOutcome.NOT_READY:
        return ReadinessOutcome.NOT_READY
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


def _cmd_pane_serialized_readiness_signal(
    backend,
    pane_id: str,
    foreground_command: str,
) -> ReadinessOutcome | None:
    if os.environ.get('CCB_CMD_READINESS_SIGNAL', '0') != '1':
        return None
    getter = getattr(backend, 'get_ccb_ready_signal', None)
    if not callable(getter):
        return None
    try:
        signal = str(getter(pane_id, foreground_command) or '').strip().lower()
    except Exception:
        return ReadinessOutcome.PROBE_UNAVAILABLE
    if signal == 'ready':
        return ReadinessOutcome.READY
    if signal in {'busy', 'not_ready', 'blocked', 'modal'}:
        return ReadinessOutcome.NOT_READY
    return None


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


# v8.4 PR 7 (Shape A 2026-05-05) — fresh-at-send invariant.
#
# Pre-fix: this resolver held a 30-second TTL cache of the cmd pane id on
# the dispatcher. The cache survived pane replacement (supervisor or user
# closing+reopening claude in the cmd window), so dispatcher would inject
# into a pane id that no longer existed and tmux would respond with
# "target pane has exited". The live diag in PR #8 confirmed this
# (`cached_pane_id=None, fresh_pane_id='%3', is_alive_fresh=True,
# pending_cmd_replies=326` — fresh lookup found a live pane while the
# delivery path was still sending to a dead one).
#
# Post-fix: every call resolves fresh from tmux metadata via
# `_lookup_cmd_pane_id`. The only sanctioned cache window is the local
# `pane_state` list inside `_deliver_cmd_replies_impl`, which is reset
# every sweep tick. `_invalidate_cmd_pane_cache` survives as a no-op for
# diagnostic compatibility (the diag emitter at line 815+ inspects
# `_cmd_pane_cache` to compare cached vs. fresh; tests still inject a
# value into that attribute to exercise the comparison path).
def _discover_cmd_pane_id(dispatcher) -> str | None:
    layout = getattr(dispatcher, '_layout', None)
    if layout is None:
        return None
    return _lookup_cmd_pane_id(dispatcher, layout)


def _invalidate_cmd_pane_cache(dispatcher):
    """Legacy hook. Pre-Shape-A this cleared the cross-sweep TTL cache;
    post-Shape-A there is no cross-sweep cache to clear, so this is a
    no-op. Retained because diag tests inject a sentinel into
    `_cmd_pane_cache` and want a known clear path. Within-sweep
    invalidation is handled inline in `_deliver_cmd_replies_impl`.
    """
    try:
        dispatcher._cmd_pane_cache = None
    except AttributeError:
        pass


def _max_cmd_pane_retries() -> int:
    raw = str(os.environ.get(_CMD_PANE_RETRY_ENV, '') or '').strip()
    if not raw:
        return _CMD_PANE_RETRY_DEFAULT
    try:
        value = int(raw)
    except ValueError:
        return _CMD_PANE_RETRY_DEFAULT
    return max(1, value)


def _get_pane_retry_counts(dispatcher) -> dict:
    counts = getattr(dispatcher, '_cmd_pane_retry_counts', None)
    if counts is None:
        counts = {}
        try:
            dispatcher._cmd_pane_retry_counts = counts
        except AttributeError:
            pass
    return counts


def _bump_pane_retry_count(dispatcher, event_id: str) -> int:
    counts = _get_pane_retry_counts(dispatcher)
    new = int(counts.get(event_id, 0)) + 1
    counts[event_id] = new
    return new


def _clear_pane_retry_count(dispatcher, event_id: str) -> None:
    counts = getattr(dispatcher, '_cmd_pane_retry_counts', None)
    if counts is not None:
        counts.pop(event_id, None)


def _prune_pane_retry_counts(dispatcher, pending) -> None:
    """Drop K-counter entries for events that are no longer pending. Keeps
    the dict bounded across long-lived daemons even when events disappear
    via ack/abandon paths that do not reach the delivery loop."""
    counts = getattr(dispatcher, '_cmd_pane_retry_counts', None)
    if not counts:
        return
    pending_ids = {getattr(e, 'inbound_event_id', None) for e in (pending or ())}
    pending_ids.discard(None)
    for stale in [k for k in counts if k not in pending_ids]:
        counts.pop(stale, None)


def _maybe_emit_cmd_pane_discovery_diag(dispatcher, *, pending_count: int) -> None:
    global _cmd_pane_diag_emitted
    if _cmd_pane_diag_emitted:
        return
    _cmd_pane_diag_emitted = True

    cached = getattr(dispatcher, '_cmd_pane_cache', None)
    cached_pane_id = None
    cached_age = None
    if cached is not None:
        try:
            cached_pane_id, cached_at = cached
            cached_age = time.monotonic() - float(cached_at)
        except Exception:
            cached_pane_id, cached_age = None, None

    layout = getattr(dispatcher, '_layout', None)
    fresh_pane_id = None
    if layout is not None:
        try:
            fresh_pane_id = _lookup_cmd_pane_id(dispatcher, layout)
        except Exception:
            _logger.debug('v8.4-diag fresh discovery raised', exc_info=True)
            fresh_pane_id = None

    is_alive_cached = None
    is_alive_fresh = None
    backend = None
    try:
        backend = _get_tmux_backend(dispatcher)
    except Exception:
        _logger.debug('v8.4-diag backend lookup raised', exc_info=True)
        backend = None
    if backend is not None:
        if cached_pane_id:
            try:
                is_alive_cached = bool(backend.is_alive(cached_pane_id))
            except Exception:
                is_alive_cached = None
        if fresh_pane_id and fresh_pane_id != cached_pane_id:
            try:
                is_alive_fresh = bool(backend.is_alive(fresh_pane_id))
            except Exception:
                is_alive_fresh = None
        elif fresh_pane_id and fresh_pane_id == cached_pane_id:
            is_alive_fresh = is_alive_cached

    bootstrap_pane_id, bootstrap_report_path = _read_startup_report_bootstrap_pane(dispatcher)

    _logger.warning(
        'v8.4-diag cmd-pane-discovery cached_pane_id=%r cached_age_s=%s '
        'fresh_pane_id=%r is_alive_cached=%s is_alive_fresh=%s '
        'bootstrap_cmd_pane=%r bootstrap_report_path=%s '
        'pending_cmd_replies=%s '
        'note=bootstrap_cmd_pane_may_be_supervisor_overwritten',
        cached_pane_id,
        f'{cached_age:.3f}' if cached_age is not None else None,
        fresh_pane_id,
        is_alive_cached,
        is_alive_fresh,
        bootstrap_pane_id,
        bootstrap_report_path,
        pending_count,
    )


def _read_startup_report_bootstrap_pane(dispatcher) -> tuple[str | None, str | None]:
    layout = getattr(dispatcher, '_layout', None)
    if layout is None:
        return None, None
    project_root = getattr(layout, 'project_root', None)
    if project_root is None:
        return None, None
    candidate = Path(project_root) / '.ccb' / 'ccbd' / 'startup-report.json'
    if not candidate.exists():
        return None, str(candidate)
    try:
        with candidate.open('r', encoding='utf-8') as handle:
            data = json.load(handle)
    except Exception:
        return None, str(candidate)
    bootstrap = data.get('bootstrap_cmd_pane') if isinstance(data, dict) else None
    if bootstrap is None and isinstance(data, dict):
        actions = data.get('actions_taken') or data.get('actions')
        if isinstance(actions, list):
            for entry in actions:
                text = str(entry or '')
                if text.startswith('bootstrap_cmd_pane:'):
                    bootstrap = text.split(':', 1)[1] or None
                    break
    return (str(bootstrap) if bootstrap else None), str(candidate)


def _count_pending_cmd_replies(dispatcher) -> int:
    control = getattr(dispatcher, '_message_bureau_control', None)
    kernel = getattr(control, '_mailbox_kernel', None) if control is not None else None
    if kernel is None:
        return -1
    try:
        pending = kernel.pending_events('cmd', event_type=InboundEventType.TASK_REPLY)
    except Exception:
        return -1
    try:
        return len(pending or ())
    except TypeError:
        return -1


def reset_cmd_pane_diag_for_test() -> None:
    """Test helper. Resets the one-shot gate so tests can simulate restart."""
    global _cmd_pane_diag_emitted
    _cmd_pane_diag_emitted = False


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
