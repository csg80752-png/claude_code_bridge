"""Regression tests for the v8.3.3 cmd-pending-sweep fix.

Pre-fix behavior: `_deliver_cmd_replies` processed only the head event from
`kernel.head_pending_event('cmd')`. After a successful inject, the reply_id
was added to `injected_cache` and subsequent ticks would early-return on
cache hit, leaving the head pinned indefinitely. Real replies (not
heartbeats) cannot auto-ack — the codex 2026-04-22 contract leaves head
consume to the human via `ccb ack cmd <iev>`. Result: a single un-acked
real reply blocked every subsequent reply, even though all of them were
independently injectable into the cmd pane.

Post-fix behavior: per dispatcher tick, sweep all pending non-terminal
task_reply events. The cache acts only as the per-event idempotency guard,
not as a head-pin. The codex 2026-04-22 contract is preserved — the
dispatcher still does not consume head for real replies; only suppressed
events (heartbeats / cancelled-empty) are auto-acked via `_try_ack`, and
only when they are actually at head.

v8.3.3 R2 ordering preservation (codex review [P2] 2026-05-03 KST): the
sweep only skips ahead past events that are fully resolved this tick
(cache hit, abandoned malformed payload OR abandoned planning exception,
suppressed-and-acked). Any TRANSIENT deferred outcome (gate hold,
reply_store race, pane unavailability) STOPS the sweep so a later reply
never overtakes an earlier reply still waiting to surface.

v8.3.3 R3 (codex review [P2] 2026-05-03 KST): planning exceptions are
terminal not deferred — abandoned via kernel.abandon so deterministic
failures (oversize body etc.) cannot wedge the queue forever. The
phase-2 failure record is still emitted for telemetry.

v8.3.3 R4 (codex review [P2] 2026-05-03 KST): if kernel.abandon itself
fails (mailbox I/O error), the event remains non-terminal, so the
sweep must STOP rather than `continue`. Unconditional continue would
let a later reply inject ahead of the still-unresolved event,
re-introducing the R2 ordering bug under abandon failure.
"""
from pathlib import Path
from types import SimpleNamespace

from ccbd.services.dispatcher_runtime.reply_delivery_runtime import preparation_service as cmd_replies
from mailbox_kernel import (
    InboundEventRecord,
    InboundEventStatus,
    InboundEventStore,
    InboundEventType,
    MailboxKernelService,
)
from message_bureau.models import ReplyRecord
from message_bureau.model_enums import ReplyTerminalStatus
from message_bureau.store import ReplyStore
from storage.paths import PathLayout


class _Backend:
    def __init__(self):
        self.sent = []

    def is_alive(self, pane_id):
        return True

    def send_text_to_pane(self, pane_id, text, **kwargs):
        self.sent.append((pane_id, text))


def _reply_event(reply_id, iev):
    return InboundEventRecord(
        inbound_event_id=iev,
        agent_name='cmd',
        event_type=InboundEventType.TASK_REPLY,
        message_id=f'msg-{reply_id}',
        attempt_id=f'attempt-{reply_id}',
        payload_ref=f'reply:{reply_id}',
        priority=10,
        status=InboundEventStatus.QUEUED,
        created_at='2026-05-04T00:00:00Z',
    )


def _heartbeat_reply(reply_id, agent='agent1'):
    return ReplyRecord(
        reply_id=reply_id,
        message_id=f'msg-{reply_id}',
        attempt_id=f'attempt-{reply_id}',
        agent_name=agent,
        terminal_status=ReplyTerminalStatus.INCOMPLETE,
        reply=f'CCB_HEARTBEAT from={agent}',
        diagnostics={'notice': True, 'notice_kind': 'heartbeat'},
        finished_at='2026-05-04T00:00:01Z',
    )


def _real_reply(reply_id, agent='agent1'):
    return ReplyRecord(
        reply_id=reply_id,
        message_id=f'msg-{reply_id}',
        attempt_id=f'attempt-{reply_id}',
        agent_name=agent,
        terminal_status=ReplyTerminalStatus.COMPLETED,
        reply=f'real reply body for {reply_id}',
        diagnostics={},
        finished_at='2026-05-04T00:00:01Z',
    )


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv('CCB_CCBD_READAMP_CACHE', '1')
    monkeypatch.setenv('CCB_CMD_READY_GATE', '0')
    layout = PathLayout(Path(tmp_path) / 'repo')
    inbox = layout.agent_inbox_path('cmd')
    inbox.parent.mkdir(parents=True, exist_ok=True)
    inbox.touch()

    inbound_store = InboundEventStore(layout)
    kernel = MailboxKernelService(
        layout,
        clock=lambda: '2026-05-04T00:00:00Z',
        inbound_store=inbound_store,
    )
    reply_store = ReplyStore(layout)

    backend = _Backend()
    monkeypatch.setattr(cmd_replies, '_discover_cmd_pane_id', lambda dispatcher: 'cmd-pane')
    monkeypatch.setattr(cmd_replies, '_get_tmux_backend', lambda dispatcher: backend)
    monkeypatch.setattr(cmd_replies, '_cmd_pane_foreground_command', lambda backend, pane_id: 'claude')
    monkeypatch.setattr(
        cmd_replies,
        'plan_cmd_delivery',
        lambda dispatcher, reply, project_root, body_store, **kwargs: (
            SimpleNamespace(body=reply.reply, header_only=False, body_file=None),
            None,
        ),
    )

    dispatcher = SimpleNamespace(
        _message_bureau_control=SimpleNamespace(_mailbox_kernel=kernel, _reply_store=reply_store),
        _layout=SimpleNamespace(project_root=Path(tmp_path)),
        _clock=lambda: '2026-05-04T00:00:02Z',
    )

    return dispatcher, kernel, inbound_store, reply_store, backend


def test_deliver_cmd_replies_sweeps_all_pending_in_one_tick(monkeypatch, tmp_path):
    """A single tick must inject every pending real reply, not just head."""
    dispatcher, kernel, inbound_store, reply_store, backend = _setup(monkeypatch, tmp_path)

    # Order: heartbeat_A (head), real_A, heartbeat_B, real_B
    inbound_store.append(_reply_event('reply-hb-A', 'evt-hb-A'))
    inbound_store.append(_reply_event('reply-real-A', 'evt-real-A'))
    inbound_store.append(_reply_event('reply-hb-B', 'evt-hb-B'))
    inbound_store.append(_reply_event('reply-real-B', 'evt-real-B'))
    reply_store.append(_heartbeat_reply('reply-hb-A'))
    reply_store.append(_real_reply('reply-real-A'))
    reply_store.append(_heartbeat_reply('reply-hb-B'))
    reply_store.append(_real_reply('reply-real-B'))

    cmd_replies._deliver_cmd_replies(dispatcher)

    # Only the two real replies should be injected — heartbeats are suppressed.
    assert len(backend.sent) == 2
    bodies = [text for _, text in backend.sent]
    assert any('reply-real-A' in body for body in bodies)
    assert any('reply-real-B' in body for body in bodies)
    assert not any('CCB_HEARTBEAT' in body for body in bodies)

    # heartbeat_A was at head → _try_ack matched head → CONSUMED.
    assert inbound_store.get_latest('cmd', 'evt-hb-A').status == InboundEventStatus.CONSUMED

    # Real replies were visible-injected and consumed, so mailbox metrics drain.
    assert inbound_store.get_latest('cmd', 'evt-real-A').status == InboundEventStatus.CONSUMED
    assert inbound_store.get_latest('cmd', 'evt-real-B').status == InboundEventStatus.CONSUMED

    # heartbeat_B is also consumed in the same sweep once the visible real
    # replies ahead of it have drained.
    assert inbound_store.get_latest('cmd', 'evt-hb-B').status == InboundEventStatus.CONSUMED

    # Cache contains both real replies, no heartbeats (suppressed before cache add).
    cache = cmd_replies._get_injected_cache(dispatcher)
    assert 'reply-real-A' in cache
    assert 'reply-real-B' in cache
    assert 'reply-hb-A' not in cache
    assert 'reply-hb-B' not in cache


def test_deliver_cmd_replies_sweep_is_idempotent_across_ticks(monkeypatch, tmp_path):
    """A second sweep with no new pending must not re-inject (cache hit)."""
    dispatcher, kernel, inbound_store, reply_store, backend = _setup(monkeypatch, tmp_path)

    inbound_store.append(_reply_event('reply-real-A', 'evt-real-A'))
    inbound_store.append(_reply_event('reply-real-B', 'evt-real-B'))
    reply_store.append(_real_reply('reply-real-A'))
    reply_store.append(_real_reply('reply-real-B'))

    cmd_replies._deliver_cmd_replies(dispatcher)
    assert len(backend.sent) == 2

    # Same dispatcher state, second tick: cache hits, no new injects.
    cmd_replies._deliver_cmd_replies(dispatcher)
    assert len(backend.sent) == 2


def test_deliver_cmd_replies_chains_heartbeat_acks_in_one_tick(monkeypatch, tmp_path):
    """After visible real reply delivery drains, subsequent heartbeats auto-ack."""
    dispatcher, kernel, inbound_store, reply_store, backend = _setup(monkeypatch, tmp_path)

    # Order: real_A (head), heartbeat_B, heartbeat_C
    inbound_store.append(_reply_event('reply-real-A', 'evt-real-A'))
    inbound_store.append(_reply_event('reply-hb-B', 'evt-hb-B'))
    inbound_store.append(_reply_event('reply-hb-C', 'evt-hb-C'))
    reply_store.append(_real_reply('reply-real-A'))
    reply_store.append(_heartbeat_reply('reply-hb-B'))
    reply_store.append(_heartbeat_reply('reply-hb-C'))

    # Tick 1: real_A injected and consumed; heartbeats then drain in the same sweep.
    cmd_replies._deliver_cmd_replies(dispatcher)
    assert len(backend.sent) == 1
    assert inbound_store.get_latest('cmd', 'evt-real-A').status == InboundEventStatus.CONSUMED
    assert inbound_store.get_latest('cmd', 'evt-hb-B').status == InboundEventStatus.CONSUMED
    assert inbound_store.get_latest('cmd', 'evt-hb-C').status == InboundEventStatus.CONSUMED

    # A later tick does not re-inject or re-ack drained items.
    cmd_replies._deliver_cmd_replies(dispatcher)
    assert len(backend.sent) == 1


def test_deliver_cmd_replies_stops_sweep_on_pre_plan_hold(monkeypatch, tmp_path):
    """[P2] codex 2026-05-03: a pre-plan gate hold on r1 must NOT let r2 inject.

    Pre-fix sweep: hold(r1) → continue → gate(r2)=ready → inject(r2).
    Post-fix sweep: hold(r1) → break. r2 never touched this tick.
    """
    dispatcher, kernel, inbound_store, reply_store, backend = _setup(monkeypatch, tmp_path)

    held = []
    monkeypatch.setattr(
        cmd_replies,
        '_hold_cmd_delivery',
        lambda dispatcher, reply_id, **kw: held.append(reply_id),
    )
    gate_calls = []

    def fake_gate(backend_arg, pane_id, *, project_root):
        gate_calls.append(pane_id)
        # First call (r1) returns hold; any further call (r2) would return ready.
        if len(gate_calls) == 1:
            return (False, 'claude', 'pane_busy')
        return (True, 'claude', None)

    monkeypatch.setattr(cmd_replies, '_cmd_delivery_gate', fake_gate)

    inbound_store.append(_reply_event('reply-r1', 'evt-r1'))
    inbound_store.append(_reply_event('reply-r2', 'evt-r2'))
    reply_store.append(_real_reply('reply-r1'))
    reply_store.append(_real_reply('reply-r2'))

    cmd_replies._deliver_cmd_replies(dispatcher)

    assert held == ['reply-r1'], 'r1 should be held'
    assert len(gate_calls) == 1, 'sweep must stop after r1 hold; r2 gate must NOT be probed'
    assert backend.sent == [], 'no inject should fire when r1 is held'

    cache = cmd_replies._get_injected_cache(dispatcher)
    assert 'reply-r1' not in cache
    assert 'reply-r2' not in cache, '[P2] regression: r2 must not skip ahead of held r1'


def test_deliver_cmd_replies_stops_sweep_on_post_plan_hold(monkeypatch, tmp_path):
    """A post-plan gate hold on r1 must also stop the sweep, not skip to r2.

    Same ordering-preservation reasoning as the pre-plan case.
    """
    dispatcher, kernel, inbound_store, reply_store, backend = _setup(monkeypatch, tmp_path)

    held = []
    monkeypatch.setattr(
        cmd_replies,
        '_hold_cmd_delivery',
        lambda dispatcher, reply_id, **kw: held.append(reply_id),
    )
    gate_calls = []

    def fake_gate(backend_arg, pane_id, *, project_root):
        gate_calls.append(pane_id)
        # First call: pre-plan, ready. Second call: post-plan for r1, hold.
        # Any further call (would be r2's pre-plan) returns ready.
        if len(gate_calls) == 2:
            return (False, 'claude', 'pane_busy_post_plan')
        return (True, 'claude', None)

    monkeypatch.setattr(cmd_replies, '_cmd_delivery_gate', fake_gate)

    inbound_store.append(_reply_event('reply-r1', 'evt-r1'))
    inbound_store.append(_reply_event('reply-r2', 'evt-r2'))
    reply_store.append(_real_reply('reply-r1'))
    reply_store.append(_real_reply('reply-r2'))

    cmd_replies._deliver_cmd_replies(dispatcher)

    assert held == ['reply-r1'], 'r1 should be held on post-plan check'
    assert len(gate_calls) == 2, 'sweep must stop after r1 post-plan hold; no r2 gate probe'
    assert backend.sent == [], 'no inject should fire when r1 is post-plan held'


def test_deliver_cmd_replies_stops_sweep_on_reply_store_race(monkeypatch, tmp_path):
    """If r1's inbound event has no reply yet (race), r2 must not skip ahead.

    The reply will land soon; we wait one tick rather than reorder the cmd pane.
    """
    dispatcher, kernel, inbound_store, reply_store, backend = _setup(monkeypatch, tmp_path)

    inbound_store.append(_reply_event('reply-r1', 'evt-r1'))
    inbound_store.append(_reply_event('reply-r2', 'evt-r2'))
    # Only r2 has a reply record (writer for r1 hasn't flushed yet).
    reply_store.append(_real_reply('reply-r2'))

    cmd_replies._deliver_cmd_replies(dispatcher)

    assert backend.sent == [], 'r2 must not inject ahead of unwritten r1'


def test_deliver_cmd_replies_abandons_on_planning_exception(monkeypatch, tmp_path):
    """[P2] codex 2026-05-03 R3: a planning exception on r1 must abandon r1
    and let the sweep continue, NOT block the queue.

    Pre-R3 behavior: break on plan exception → if r1's failure is
    deterministic (oversize body, etc.) the queue wedges forever.
    Post-R3 behavior: record phase-2 failure → kernel.abandon(r1) →
    continue. r2 injects, r1 is removed from the queue (CONSUMED status
    via abandon). Retry-on-transient-failure is sacrificed for
    progress-on-deterministic-failure.
    """
    dispatcher, kernel, inbound_store, reply_store, backend = _setup(monkeypatch, tmp_path)

    plan_calls = []

    def raising_plan(dispatcher_arg, reply, project_root, body_store, **kwargs):
        plan_calls.append(reply.reply_id)
        if reply.reply_id == 'reply-r1':
            raise RuntimeError('synthetic deterministic planning failure (e.g. oversize body)')
        return (
            SimpleNamespace(body=reply.reply, header_only=False, body_file=None),
            None,
        )

    monkeypatch.setattr(cmd_replies, 'plan_cmd_delivery', raising_plan)

    inbound_store.append(_reply_event('reply-r1', 'evt-r1'))
    inbound_store.append(_reply_event('reply-r2', 'evt-r2'))
    reply_store.append(_real_reply('reply-r1'))
    reply_store.append(_real_reply('reply-r2'))

    cmd_replies._deliver_cmd_replies(dispatcher)

    # r1 plan was attempted (and raised). Sweep continued to r2 instead
    # of blocking — r2's plan was attempted and succeeded.
    assert plan_calls == ['reply-r1', 'reply-r2'], (
        'sweep must continue past abandoned r1 to attempt r2'
    )
    # r2's body landed in the pane.
    assert len(backend.sent) == 1
    assert 'reply-r2' in backend.sent[0][1]

    # r1 was abandoned (terminal status, removed from QUEUED set).
    r1_status = inbound_store.get_latest('cmd', 'evt-r1').status
    assert r1_status != InboundEventStatus.QUEUED, (
        f'r1 must be abandoned to terminal status to unblock queue, got {r1_status}'
    )

    # r2 was visible-injected and consumed, so the bad r1 does not leave
    # subsequent delivered replies pinned in the queue.
    assert inbound_store.get_latest('cmd', 'evt-r2').status == InboundEventStatus.CONSUMED


def test_deliver_cmd_replies_planning_failure_with_held_predecessor(monkeypatch, tmp_path):
    """Combined invariant: held r1 stops sweep BEFORE r2's planning failure
    can be tested. Confirms the R2 break-on-hold still wins over R3
    abandon-on-plan-exception when the hold is the earlier event.
    """
    dispatcher, kernel, inbound_store, reply_store, backend = _setup(monkeypatch, tmp_path)

    held = []
    monkeypatch.setattr(
        cmd_replies,
        '_hold_cmd_delivery',
        lambda dispatcher, reply_id, **kw: held.append(reply_id),
    )

    def fake_gate(backend_arg, pane_id, *, project_root):
        # r1's pre-plan gate holds; sweep should break here.
        return (False, 'claude', 'pane_busy')

    monkeypatch.setattr(cmd_replies, '_cmd_delivery_gate', fake_gate)

    plan_calls = []

    def raising_plan(dispatcher_arg, reply, project_root, body_store, **kwargs):
        plan_calls.append(reply.reply_id)
        raise RuntimeError('would deterministically fail on r2, but r2 should never plan')

    monkeypatch.setattr(cmd_replies, 'plan_cmd_delivery', raising_plan)

    inbound_store.append(_reply_event('reply-r1', 'evt-r1'))
    inbound_store.append(_reply_event('reply-r2', 'evt-r2'))
    reply_store.append(_real_reply('reply-r1'))
    reply_store.append(_real_reply('reply-r2'))

    cmd_replies._deliver_cmd_replies(dispatcher)

    assert held == ['reply-r1']
    assert plan_calls == [], 'r2 must not be reached when r1 is the held predecessor'
    assert backend.sent == []


def test_deliver_cmd_replies_stops_sweep_on_abandon_failure(monkeypatch, tmp_path):
    """[P2] codex 2026-05-03 R4: if abandoning a plan-failed event itself
    fails (mailbox kernel I/O error), the sweep must STOP — not continue
    past r1 to inject r2. Otherwise we re-introduce the R2 ordering bug
    under abandon failure: r1 stays non-terminal in the queue while r2
    surfaces in the cmd pane ahead of it.

    Pre-R4 behavior: try/except around kernel.abandon swallows the
    failure and the loop continues. r2 injects, r1 stays QUEUED, ordering
    is silently violated.
    Post-R4 behavior: abandon success is tracked via a flag; on failure
    the sweep breaks, leaving r1 head-of-line as expected. The next
    dispatcher tick retries the abandon (transient I/O may recover) or
    surfaces the wedge for triage.
    """
    dispatcher, kernel, inbound_store, reply_store, backend = _setup(monkeypatch, tmp_path)

    plan_calls = []

    def raising_plan(dispatcher_arg, reply, project_root, body_store, **kwargs):
        plan_calls.append(reply.reply_id)
        if reply.reply_id == 'reply-r1':
            raise RuntimeError('synthetic deterministic planning failure')
        return (
            SimpleNamespace(body=reply.reply, header_only=False, body_file=None),
            None,
        )

    monkeypatch.setattr(cmd_replies, 'plan_cmd_delivery', raising_plan)

    abandon_calls = []

    def boom_abandon(mailbox, iev, **kw):
        abandon_calls.append((mailbox, iev))
        raise RuntimeError('synthetic mailbox kernel I/O failure during abandon')

    monkeypatch.setattr(kernel, 'abandon', boom_abandon)

    inbound_store.append(_reply_event('reply-r1', 'evt-r1'))
    inbound_store.append(_reply_event('reply-r2', 'evt-r2'))
    reply_store.append(_real_reply('reply-r1'))
    reply_store.append(_real_reply('reply-r2'))

    cmd_replies._deliver_cmd_replies(dispatcher)

    # r1 plan was attempted (and raised), abandon was attempted (and raised),
    # sweep stopped — r2 was never reached.
    assert plan_calls == ['reply-r1'], (
        'sweep must stop after r1 abandon failure; r2 plan must NOT be invoked'
    )
    assert abandon_calls == [('cmd', 'evt-r1')], (
        'abandon should be invoked exactly once for r1 with cmd mailbox'
    )
    assert backend.sent == [], (
        '[P2] regression: r2 must not inject ahead of r1 when r1 abandon failed'
    )

    # r1 stays QUEUED (non-terminal) so the next dispatcher tick can retry.
    assert inbound_store.get_latest('cmd', 'evt-r1').status == InboundEventStatus.QUEUED
    # r2 untouched, still QUEUED.
    assert inbound_store.get_latest('cmd', 'evt-r2').status == InboundEventStatus.QUEUED

    # No cache entries — neither reply was successfully resolved this tick.
    cache = cmd_replies._get_injected_cache(dispatcher)
    assert 'reply-r1' not in cache
    assert 'reply-r2' not in cache
