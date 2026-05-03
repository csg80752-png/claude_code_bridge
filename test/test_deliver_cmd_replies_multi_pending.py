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

    def send_text_to_pane(self, pane_id, text):
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

    # Real replies stay QUEUED (codex 2026-04-22 contract: human acks head).
    assert inbound_store.get_latest('cmd', 'evt-real-A').status == InboundEventStatus.QUEUED
    assert inbound_store.get_latest('cmd', 'evt-real-B').status == InboundEventStatus.QUEUED

    # heartbeat_B: _try_ack invoked but head advanced to real_A after hb_A
    # consumed; head_match check rejects so hb_B stays QUEUED until real_A
    # is human-acked and hb_B becomes head on a later tick.
    assert inbound_store.get_latest('cmd', 'evt-hb-B').status == InboundEventStatus.QUEUED

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


def test_deliver_cmd_replies_chains_heartbeat_acks_across_ticks(monkeypatch, tmp_path):
    """After human acks the head real reply, subsequent heartbeats auto-ack on next tick."""
    dispatcher, kernel, inbound_store, reply_store, backend = _setup(monkeypatch, tmp_path)

    # Order: real_A (head), heartbeat_B, heartbeat_C
    inbound_store.append(_reply_event('reply-real-A', 'evt-real-A'))
    inbound_store.append(_reply_event('reply-hb-B', 'evt-hb-B'))
    inbound_store.append(_reply_event('reply-hb-C', 'evt-hb-C'))
    reply_store.append(_real_reply('reply-real-A'))
    reply_store.append(_heartbeat_reply('reply-hb-B'))
    reply_store.append(_heartbeat_reply('reply-hb-C'))

    # Tick 1: real_A injected; heartbeats stay QUEUED (not at head).
    cmd_replies._deliver_cmd_replies(dispatcher)
    assert len(backend.sent) == 1
    assert inbound_store.get_latest('cmd', 'evt-real-A').status == InboundEventStatus.QUEUED
    assert inbound_store.get_latest('cmd', 'evt-hb-B').status == InboundEventStatus.QUEUED
    assert inbound_store.get_latest('cmd', 'evt-hb-C').status == InboundEventStatus.QUEUED

    # Human acks head (real_A) — simulate the user's `ccb ack cmd evt-real-A`.
    kernel.ack_reply('cmd', 'evt-real-A', finished_at='2026-05-04T00:00:03Z')
    assert inbound_store.get_latest('cmd', 'evt-real-A').status == InboundEventStatus.CONSUMED

    # Tick 2: heartbeat_B becomes head → _try_ack consumes it. Then on the
    # next iteration, heartbeat_C's `_try_ack` re-fetches `head_pending_event`
    # which now points at hb_C (hb_B already CONSUMED), so the head_match
    # check passes and hb_C is ack'd in the same tick. Sweep delivers the
    # full heartbeat chain in one pass — a strict improvement over the
    # pre-fix one-per-tick behavior.
    cmd_replies._deliver_cmd_replies(dispatcher)
    assert inbound_store.get_latest('cmd', 'evt-hb-B').status == InboundEventStatus.CONSUMED
    assert inbound_store.get_latest('cmd', 'evt-hb-C').status == InboundEventStatus.CONSUMED

    # No additional injects on tick 2 (heartbeats are suppressed).
    assert len(backend.sent) == 1
