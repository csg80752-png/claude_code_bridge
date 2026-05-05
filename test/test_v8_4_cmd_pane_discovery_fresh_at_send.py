"""v8.4 PR 7 (Issue #3) — cmd pane fresh-at-send + retry/abandon tests.

Locks the two fix shapes:
- Shape A: fresh-at-send invariant. The cross-sweep TTL cache for
  cmd pane id is removed; every sweep resolves fresh from tmux
  metadata via _lookup_cmd_pane_id.
- Shape B: retry-once on liveness/send failure within the sweep,
  K=3 consecutive sweep stops on the same inbound event triggers
  phase-2 abandon (env var CCB_CMD_REPLY_MAX_RETRIES override).

These tests use SimpleNamespace fakes plus a mock TmuxBackend with
controllable pane lifecycle (pane_alive_map mutates between calls).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from mailbox_kernel import InboundEventStatus, InboundEventType
from ccbd.services.dispatcher_runtime.reply_delivery_runtime import preparation_service


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class _Kernel:
    def __init__(self, head):
        self._head = head
        self.calls: list[tuple[str, str]] = []

    def _is_pending(self):
        return self._head is not None and self._head.status in (
            InboundEventStatus.CREATED,
            InboundEventStatus.QUEUED,
        )

    def pending_events(self, agent_name: str, *, event_type=None):
        if not self._is_pending():
            return ()
        if event_type is not None and self._head.event_type is not event_type:
            return ()
        return (self._head,)

    def head_pending_event(self, agent_name: str):
        return self._head if self._is_pending() else None

    def abandon(self, agent_name: str, inbound_event_id: str, *, finished_at=None):
        self.calls.append(('abandon', inbound_event_id))
        if self._head is not None and self._head.inbound_event_id == inbound_event_id:
            self._head.status = InboundEventStatus.ABANDONED
        return self._head

    def ack_reply(self, agent_name: str, inbound_event_id: str, *, started_at=None, finished_at=None):
        self.calls.append(('ack_reply', inbound_event_id))
        if self._head is not None and self._head.inbound_event_id == inbound_event_id:
            self._head.status = InboundEventStatus.CONSUMED
        return self._head


class _MockTmuxBackend:
    """Mock backend with mutable pane lifecycle and scripted send failures.

    `pane_alive_map` is mutated by tests to flip pane death/creation.
    `send_raises` is a list of exceptions popped per send call; `None`
    entries mean "let this send succeed". After the list empties, the
    send still requires `is_alive` to be True for the target pane.
    """

    def __init__(self, *, pane_alive_map=None, send_raises=None, fg_command='claude'):
        self.pane_alive_map = dict(pane_alive_map or {'%1': True})
        self.send_raises = list(send_raises or [])
        self.fg_command = fg_command
        self.injected: list[tuple[str, str]] = []
        self.is_alive_calls: list[str] = []

    def is_alive(self, pane_id: str) -> bool:
        self.is_alive_calls.append(pane_id)
        return bool(self.pane_alive_map.get(pane_id, False))

    def send_text_to_pane(self, pane_id: str, text: str, **kwargs):
        if self.send_raises:
            exc = self.send_raises.pop(0)
            if exc is not None:
                raise exc
        if not self.pane_alive_map.get(pane_id, False):
            raise RuntimeError(f'target pane has exited (pane_id={pane_id})')
        self.injected.append((pane_id, text))

    def get_pane_content(self, pane_id: str, lines: int = 120) -> str:
        return '> '


def _make_head(*, status=InboundEventStatus.QUEUED, payload_ref='reply:rep-1', evt_id='evt-1'):
    return SimpleNamespace(
        inbound_event_id=evt_id,
        event_type=InboundEventType.TASK_REPLY,
        status=status,
        payload_ref=payload_ref,
    )


def _make_reply(*, reply_id='rep-1', body='cmd reply text'):
    return SimpleNamespace(
        attempt_id='att-1',
        agent_name='codex',
        reply_id=reply_id,
        terminal_status=SimpleNamespace(value='succeeded'),
        diagnostics={},
        reply=body,
    )


def _make_dispatcher(
    *,
    head,
    reply,
    project_root: Path | None = None,
    clock: str = '2026-05-05T00:00:00+00:00',
    job_id: str = 'job-1',
    task_id: str = 'task-1',
):
    kernel = _Kernel(head)
    reply_store = SimpleNamespace(
        get_latest=lambda rid: reply if reply and reply.reply_id == rid else None
    )
    attempt_store = SimpleNamespace(
        get_latest=lambda aid: SimpleNamespace(job_id=job_id)
    )
    control = SimpleNamespace(
        _mailbox_kernel=kernel,
        _reply_store=reply_store,
        _attempt_store=attempt_store,
    )
    # Always produce a layout so the discovery path reaches
    # _lookup_cmd_pane_id (which the tests monkey-patch). When the test
    # does not supply a project_root, we still attach a layout namespace
    # carrying project_root=None so _resolve_project_root yields None and
    # telemetry skips disk I/O.
    layout = SimpleNamespace(project_root=project_root)
    dispatcher = SimpleNamespace(
        _message_bureau_control=control,
        _layout=layout,
        _clock=lambda: clock,
        get_job=lambda jid: SimpleNamespace(
            job_id=job_id,
            request=SimpleNamespace(task_id=task_id),
        ),
    )
    return dispatcher, kernel


def _patch_claude_foreground(monkeypatch):
    monkeypatch.setattr(
        preparation_service,
        '_cmd_pane_foreground_command',
        lambda backend, pane_id: 'claude',
    )


def _read_metrics(project_root: Path) -> list[dict]:
    path = project_root / '.ccb' / 'metrics' / 'body_read_followup.jsonl'
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding='utf-8').splitlines()
        if line.strip()
    ]


# --------------------------------------------------------------------------- #
# 1) Fresh-at-send resolution (Shape A)
# --------------------------------------------------------------------------- #


def test_fresh_at_send_resolution_ignores_persisted_cache(monkeypatch):
    """A stale (pane_id, monotonic) entry on dispatcher._cmd_pane_cache
    must NOT be used at injection time. The dispatcher must call
    _lookup_cmd_pane_id and use whatever tmux metadata says right now."""
    head = _make_head(payload_ref='reply:rep-fresh', evt_id='evt-fresh')
    reply = _make_reply(reply_id='rep-fresh', body='fresh body')
    backend = _MockTmuxBackend(pane_alive_map={'%99': True})
    dispatcher, kernel = _make_dispatcher(head=head, reply=reply)

    # Plant a stale cache from the pre-Shape-A world. Production code
    # post-fix never reads this attribute at the discovery path.
    dispatcher._cmd_pane_cache = ('%STALE-DEAD-ID', time.monotonic())

    lookup_calls: list[tuple] = []

    def _lookup(d, l):
        lookup_calls.append((d, l))
        return '%99'

    monkeypatch.setattr(preparation_service, '_lookup_cmd_pane_id', _lookup)
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend)
    _patch_claude_foreground(monkeypatch)

    preparation_service._deliver_cmd_replies(dispatcher)

    # The fresh lookup ran (cache short-circuit removed).
    assert lookup_calls, '_lookup_cmd_pane_id must be called fresh every sweep'
    # The pane id used for inject came from the fresh lookup, not the
    # planted stale cache.
    assert backend.injected and backend.injected[0][0] == '%99'
    # No cross-sweep cache rewritten on success — the only sanctioned
    # cache window is the local pane_state inside the sweep impl.
    assert getattr(dispatcher, '_cmd_pane_cache', None) == (
        '%STALE-DEAD-ID',
        dispatcher._cmd_pane_cache[1],
    ), 'production code must not overwrite _cmd_pane_cache after Shape A'
    assert kernel.calls == []


def test_fresh_at_send_each_sweep_calls_lookup_again(monkeypatch):
    """Three sweeps with three pending replies each call lookup at least
    once per sweep (TTL cache from pre-Shape-A would skip the second
    and third lookup)."""
    head = _make_head(payload_ref='reply:rep-once', evt_id='evt-once')
    reply = _make_reply(reply_id='rep-once', body='once')
    backend = _MockTmuxBackend(pane_alive_map={'%2': True})
    dispatcher, _kernel = _make_dispatcher(head=head, reply=reply)

    counter = {'n': 0}

    def _lookup(d, l):
        counter['n'] += 1
        return '%2'

    monkeypatch.setattr(preparation_service, '_lookup_cmd_pane_id', _lookup)
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend)
    _patch_claude_foreground(monkeypatch)

    # First sweep delivers and caches the reply id.
    preparation_service._deliver_cmd_replies(dispatcher)
    first = counter['n']
    assert first >= 1

    # Subsequent sweeps short-circuit on injected_cache (already
    # delivered) so the pane lookup is NOT invoked again — that's the
    # within-sweep cache. We exercise a fresh lookup by clearing the
    # injected cache (simulates LRU eviction on a long-running daemon).
    preparation_service._get_injected_cache(dispatcher).clear()
    preparation_service._deliver_cmd_replies(dispatcher)
    assert counter['n'] > first, (
        'after cache eviction, the next sweep must perform a fresh lookup '
        '(no cross-sweep TTL short-circuit)'
    )


# --------------------------------------------------------------------------- #
# 2) Retry-once within sweep on stale send (Shape B)
# --------------------------------------------------------------------------- #


def test_send_target_pane_exited_retries_once_within_sweep_and_succeeds(monkeypatch):
    """Initial send raises 'target pane has exited'; the within-sweep
    retry path re-resolves to a fresh live pane and the second send
    succeeds. No phase-2 failure recorded; K-counter cleared."""
    head = _make_head(payload_ref='reply:rep-retry', evt_id='evt-retry')
    reply = _make_reply(reply_id='rep-retry', body='retry body')

    # First send raises, second send (after fresh resolve) succeeds.
    backend = _MockTmuxBackend(
        pane_alive_map={'%2': True, '%9': True},
        send_raises=[RuntimeError('target pane has exited (pane_id=%2)')],
    )
    dispatcher, kernel = _make_dispatcher(head=head, reply=reply)

    lookup_seq = ['%2', '%9']

    def _lookup(d, l):
        return lookup_seq.pop(0) if lookup_seq else '%9'

    monkeypatch.setattr(preparation_service, '_lookup_cmd_pane_id', _lookup)
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend)
    _patch_claude_foreground(monkeypatch)

    preparation_service._deliver_cmd_replies(dispatcher)

    # Exactly one successful inject, on the fresh pane id.
    assert len(backend.injected) == 1
    assert backend.injected[0][0] == '%9', 'retry must land on fresh pane id'
    # No abandon — retry succeeded.
    assert kernel.calls == []
    # K-counter cleared.
    counts = getattr(dispatcher, '_cmd_pane_retry_counts', {})
    assert head.inbound_event_id not in counts


def test_initial_pane_dead_retry_picks_up_replacement_pane(monkeypatch):
    """First lookup returns a dead pane id; retry inside _ensure_pane_init
    re-resolves and the second lookup returns a live replacement.
    Sweep proceeds to deliver to the replacement pane without abandon."""
    head = _make_head(payload_ref='reply:rep-replace', evt_id='evt-replace')
    reply = _make_reply(reply_id='rep-replace', body='replacement')
    backend = _MockTmuxBackend(pane_alive_map={'%dead': False, '%alive': True})
    dispatcher, kernel = _make_dispatcher(head=head, reply=reply)

    lookup_seq = ['%dead', '%alive']

    def _lookup(d, l):
        return lookup_seq.pop(0) if lookup_seq else '%alive'

    monkeypatch.setattr(preparation_service, '_lookup_cmd_pane_id', _lookup)
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend)
    _patch_claude_foreground(monkeypatch)

    preparation_service._deliver_cmd_replies(dispatcher)

    assert len(backend.injected) == 1
    assert backend.injected[0][0] == '%alive'
    assert kernel.calls == []
    counts = getattr(dispatcher, '_cmd_pane_retry_counts', {})
    assert head.inbound_event_id not in counts


# --------------------------------------------------------------------------- #
# 3) K-counter abandon (Shape B)
# --------------------------------------------------------------------------- #


def test_retry_exhausted_after_K_sweep_stops_abandons_with_phase2(
    monkeypatch, tmp_path
):
    """Default K=3. Three consecutive sweep stops on the same inbound
    event trigger phase-2 abandon with reason='retry_exhausted'."""
    head = _make_head(payload_ref='reply:rep-abandon', evt_id='evt-abandon')
    reply = _make_reply(reply_id='rep-abandon', body='will be abandoned')
    backend = _MockTmuxBackend(pane_alive_map={'%dead': False})
    dispatcher, kernel = _make_dispatcher(
        head=head, reply=reply, project_root=tmp_path,
    )

    monkeypatch.setattr(preparation_service, '_lookup_cmd_pane_id', lambda d, l: '%dead')
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend)
    _patch_claude_foreground(monkeypatch)
    monkeypatch.delenv('CCB_CMD_REPLY_MAX_RETRIES', raising=False)

    # Tick 1 — pane dead → K=1.
    preparation_service._deliver_cmd_replies(dispatcher)
    assert kernel.calls == []
    assert backend.injected == []

    # Tick 2 — K=2, still no abandon.
    preparation_service._deliver_cmd_replies(dispatcher)
    assert kernel.calls == []

    # Tick 3 — K=3 → abandon with phase-2 record.
    preparation_service._deliver_cmd_replies(dispatcher)
    assert kernel.calls == [('abandon', 'evt-abandon')]
    assert backend.injected == []

    records = _read_metrics(tmp_path)
    abandons = [
        r for r in records
        if r.get('event') == 'cmd_phase2_failure'
        and r.get('reason') == 'retry_exhausted'
    ]
    assert len(abandons) == 1
    assert abandons[0]['reply_id'] == 'rep-abandon'
    assert abandons[0]['stage'] == 'abandon'
    assert abandons[0]['pane_alive'] is False

    # Counter cleared after abandon.
    counts = getattr(dispatcher, '_cmd_pane_retry_counts', {})
    assert head.inbound_event_id not in counts


def test_env_var_overrides_max_retries_to_two(monkeypatch, tmp_path):
    """CCB_CMD_REPLY_MAX_RETRIES=2 → abandon after the second sweep stop."""
    head = _make_head(payload_ref='reply:rep-k2', evt_id='evt-k2')
    reply = _make_reply(reply_id='rep-k2', body='k=2')
    backend = _MockTmuxBackend(pane_alive_map={'%dead': False})
    dispatcher, kernel = _make_dispatcher(
        head=head, reply=reply, project_root=tmp_path,
    )

    monkeypatch.setattr(preparation_service, '_lookup_cmd_pane_id', lambda d, l: '%dead')
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend)
    _patch_claude_foreground(monkeypatch)
    monkeypatch.setenv('CCB_CMD_REPLY_MAX_RETRIES', '2')

    preparation_service._deliver_cmd_replies(dispatcher)
    assert kernel.calls == []

    preparation_service._deliver_cmd_replies(dispatcher)
    assert kernel.calls == [('abandon', 'evt-k2')]


def test_env_var_invalid_value_falls_back_to_default(monkeypatch):
    """Garbage env var values fall back to the K=3 default (no crash)."""
    monkeypatch.setenv('CCB_CMD_REPLY_MAX_RETRIES', 'not-an-int')
    assert preparation_service._max_cmd_pane_retries() == 3
    monkeypatch.setenv('CCB_CMD_REPLY_MAX_RETRIES', '   ')
    assert preparation_service._max_cmd_pane_retries() == 3
    monkeypatch.setenv('CCB_CMD_REPLY_MAX_RETRIES', '0')
    # Clamped to >= 1 so a zero/negative configuration cannot cause
    # immediate abandon on the first sweep stop.
    assert preparation_service._max_cmd_pane_retries() == 1


def test_retry_counter_resets_on_successful_send(monkeypatch):
    """Two failed sweep stops bump K=2; a third sweep with a healthy
    backend delivers the reply and clears the counter, so the *next*
    failure starts again from K=1 (not K=3)."""
    head = _make_head(payload_ref='reply:rep-recover', evt_id='evt-recover')
    reply = _make_reply(reply_id='rep-recover', body='recovered')
    backend = _MockTmuxBackend(pane_alive_map={'%1': False})
    dispatcher, kernel = _make_dispatcher(head=head, reply=reply)

    monkeypatch.setattr(preparation_service, '_lookup_cmd_pane_id', lambda d, l: '%1')
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend)
    _patch_claude_foreground(monkeypatch)
    monkeypatch.setenv('CCB_CMD_REPLY_MAX_RETRIES', '3')

    preparation_service._deliver_cmd_replies(dispatcher)
    preparation_service._deliver_cmd_replies(dispatcher)
    counts = getattr(dispatcher, '_cmd_pane_retry_counts', {})
    assert counts.get(head.inbound_event_id) == 2
    assert kernel.calls == []

    # Pane heals between ticks.
    backend.pane_alive_map['%1'] = True
    preparation_service._deliver_cmd_replies(dispatcher)
    assert len(backend.injected) == 1
    counts = getattr(dispatcher, '_cmd_pane_retry_counts', {})
    assert head.inbound_event_id not in counts


# --------------------------------------------------------------------------- #
# PR #10 codex-review follow-up: K-counter is consecutive pane stops only
# --------------------------------------------------------------------------- #


def test_pane_retry_counter_resets_after_readiness_hold(monkeypatch):
    """pane_dead -> readiness_hold -> pane_dead must leave K at 1, not 2."""
    head = _make_head(payload_ref='reply:rep-readiness-reset', evt_id='evt-readiness-reset')
    reply = _make_reply(reply_id='rep-readiness-reset', body='readiness reset')
    backend = _MockTmuxBackend(pane_alive_map={'%1': False})
    dispatcher, kernel = _make_dispatcher(head=head, reply=reply)

    monkeypatch.setattr(preparation_service, '_lookup_cmd_pane_id', lambda d, l: '%1')
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend)
    _patch_claude_foreground(monkeypatch)
    monkeypatch.setenv('CCB_CMD_REPLY_MAX_RETRIES', '3')

    # Tick 1: pane-related stop, K=1.
    preparation_service._deliver_cmd_replies(dispatcher)
    assert getattr(dispatcher, '_cmd_pane_retry_counts', {}).get(head.inbound_event_id) == 1

    # Tick 2: live pane but readiness gate holds, so the pane K-counter resets.
    backend.pane_alive_map['%1'] = True
    monkeypatch.setattr(backend, 'get_pane_content', lambda pane_id, lines=120: 'still generating\n')
    preparation_service._deliver_cmd_replies(dispatcher)
    assert head.inbound_event_id not in getattr(dispatcher, '_cmd_pane_retry_counts', {})
    assert kernel.calls == []
    assert backend.injected == []

    # Tick 3: another pane stop starts a new consecutive run at K=1.
    backend.pane_alive_map['%1'] = False
    preparation_service._deliver_cmd_replies(dispatcher)
    assert kernel.calls == []
    assert getattr(dispatcher, '_cmd_pane_retry_counts', {}).get(head.inbound_event_id) == 1


def test_pane_retry_counter_still_abandons_after_three_consecutive_pane_stops(
    monkeypatch, tmp_path
):
    """The follow-up reset must not weaken the existing K=3 pane-stop abandon."""
    head = _make_head(payload_ref='reply:rep-consecutive', evt_id='evt-consecutive')
    reply = _make_reply(reply_id='rep-consecutive', body='consecutive pane stops')
    backend = _MockTmuxBackend(pane_alive_map={'%dead': False})
    dispatcher, kernel = _make_dispatcher(head=head, reply=reply, project_root=tmp_path)

    monkeypatch.setattr(preparation_service, '_lookup_cmd_pane_id', lambda d, l: '%dead')
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend)
    _patch_claude_foreground(monkeypatch)
    monkeypatch.setenv('CCB_CMD_REPLY_MAX_RETRIES', '3')

    preparation_service._deliver_cmd_replies(dispatcher)
    preparation_service._deliver_cmd_replies(dispatcher)
    assert kernel.calls == []

    preparation_service._deliver_cmd_replies(dispatcher)
    assert kernel.calls == [('abandon', 'evt-consecutive')]


def test_pane_retry_counter_preserved_when_gate_capture_fails_due_to_dead_pane(
    monkeypatch,
):
    """Option X: gate-hold branches re-check liveness. If a pane dies after
    the initial probe but before foreground capture completes, the empty
    capture is treated as a pane-related stop, so K is bumped rather than
    cleared and the event abandons at K=3.
    """
    head = _make_head(payload_ref='reply:rep-capture-dead', evt_id='evt-capture-dead')
    reply = _make_reply(reply_id='rep-capture-dead', body='capture dead')
    backend = _MockTmuxBackend(pane_alive_map={'%1': True})
    dispatcher, kernel = _make_dispatcher(head=head, reply=reply)

    monkeypatch.setattr(preparation_service, '_lookup_cmd_pane_id', lambda d, l: '%1')
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend)
    monkeypatch.setattr(preparation_service, '_cmd_pane_foreground_command', lambda b, p: '')
    monkeypatch.setattr(preparation_service, '_cmd_pane_diag_emitted', True)
    monkeypatch.setenv('CCB_CMD_REPLY_MAX_RETRIES', '3')

    alive_results = iter([
        False, False,  # Tick 1: resolve + retry both see pane_dead -> K=1.
        True, False,   # Tick 2: initial probe succeeds; gate-hold recheck sees dead -> K=2.
        True, False,   # Tick 3: same race; gate-hold recheck sees dead -> K=3 abandon.
    ])
    monkeypatch.setattr(backend, 'is_alive', lambda pane_id: next(alive_results))

    preparation_service._deliver_cmd_replies(dispatcher)
    assert getattr(dispatcher, '_cmd_pane_retry_counts', {}).get(head.inbound_event_id) == 1
    assert kernel.calls == []

    preparation_service._deliver_cmd_replies(dispatcher)
    assert getattr(dispatcher, '_cmd_pane_retry_counts', {}).get(head.inbound_event_id) == 2
    assert kernel.calls == []
    assert backend.injected == []

    preparation_service._deliver_cmd_replies(dispatcher)
    assert kernel.calls == [('abandon', 'evt-capture-dead')]
    assert head.inbound_event_id not in getattr(dispatcher, '_cmd_pane_retry_counts', {})
    assert backend.injected == []


def test_pane_retry_counter_restarts_after_safety_gate_hold(monkeypatch):
    """pane_dead -> safety holds -> pane_dead -> pane_dead is K=2, not abandon."""
    head = _make_head(payload_ref='reply:rep-safety-reset', evt_id='evt-safety-reset')
    reply = _make_reply(reply_id='rep-safety-reset', body='safety reset')
    backend = _MockTmuxBackend(pane_alive_map={'%1': False})
    dispatcher, kernel = _make_dispatcher(head=head, reply=reply)

    monkeypatch.setattr(preparation_service, '_lookup_cmd_pane_id', lambda d, l: '%1')
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend)
    monkeypatch.setenv('CCB_CMD_REPLY_MAX_RETRIES', '3')

    # Tick 1: pane-related stop, K=1.
    monkeypatch.setattr(preparation_service, '_cmd_pane_foreground_command', lambda b, p: 'claude')
    preparation_service._deliver_cmd_replies(dispatcher)
    assert getattr(dispatcher, '_cmd_pane_retry_counts', {}).get(head.inbound_event_id) == 1

    # Ticks 2-6: live pane, but unsafe foreground command holds the head.
    backend.pane_alive_map['%1'] = True
    monkeypatch.setattr(preparation_service, '_cmd_pane_foreground_command', lambda b, p: 'vim')
    for _ in range(5):
        preparation_service._deliver_cmd_replies(dispatcher)
        assert kernel.calls == []
        assert head.inbound_event_id not in getattr(dispatcher, '_cmd_pane_retry_counts', {})
    assert backend.injected == []

    # Ticks 7-8: two pane stops after the safety hold restart K at 1 then 2.
    backend.pane_alive_map['%1'] = False
    monkeypatch.setattr(preparation_service, '_cmd_pane_foreground_command', lambda b, p: 'claude')
    preparation_service._deliver_cmd_replies(dispatcher)
    assert kernel.calls == []
    assert getattr(dispatcher, '_cmd_pane_retry_counts', {}).get(head.inbound_event_id) == 1

    preparation_service._deliver_cmd_replies(dispatcher)
    assert kernel.calls == []
    assert getattr(dispatcher, '_cmd_pane_retry_counts', {}).get(head.inbound_event_id) == 2

    # Tick 9: the third consecutive pane stop after reset, fourth pane stop
    # overall, exhausts K.
    preparation_service._deliver_cmd_replies(dispatcher)
    assert kernel.calls == [('abandon', 'evt-safety-reset')]


# --------------------------------------------------------------------------- #
# 4) Daemon-alive pane replacement
# --------------------------------------------------------------------------- #


def test_daemon_alive_pane_replacement_delivers_to_new_pane_without_ttl_wait(monkeypatch):
    """Reproduces the production scenario: the cmd pane id ccbd was
    holding has died (e.g., supervisor rotated the cmd window) and a
    fresh __ccb_ctl pane has appeared under the same role+slot+project
    triplet. Without Shape A, the dispatcher waits up to 30s (TTL) before
    discovering the replacement. With Shape A, the *very next* sweep
    picks up the new pane id immediately."""
    head = _make_head(payload_ref='reply:rep-replace', evt_id='evt-replace')
    reply = _make_reply(reply_id='rep-replace', body='hello new pane')

    backend = _MockTmuxBackend(pane_alive_map={'%old': True})
    dispatcher, kernel = _make_dispatcher(head=head, reply=reply)

    discovered = ['%old']

    def _lookup(d, l):
        return discovered[0]

    monkeypatch.setattr(preparation_service, '_lookup_cmd_pane_id', _lookup)
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend)
    _patch_claude_foreground(monkeypatch)

    # Sweep 1: deliver to %old.
    preparation_service._deliver_cmd_replies(dispatcher)
    assert backend.injected == [('%old', backend.injected[0][1])]

    # Pane lifecycle event: %old exits, %new appears under the same
    # __ccb_ctl tuple. tmux metadata now returns %new for the cmd
    # role+slot+project lookup.
    backend.pane_alive_map['%old'] = False
    backend.pane_alive_map['%new'] = True
    discovered[0] = '%new'

    # New pending reply.
    next_head = _make_head(payload_ref='reply:rep-replace2', evt_id='evt-replace2')
    next_reply = _make_reply(reply_id='rep-replace2', body='post-replacement')
    dispatcher._message_bureau_control._mailbox_kernel = _Kernel(next_head)
    dispatcher._message_bureau_control._reply_store = SimpleNamespace(
        get_latest=lambda rid: next_reply if rid == next_reply.reply_id else None
    )

    # Sweep 2: must deliver to %new on the very next tick (no TTL wait).
    preparation_service._deliver_cmd_replies(dispatcher)
    assert len(backend.injected) == 2
    assert backend.injected[1][0] == '%new'


def test_send_to_replaced_pane_uses_within_sweep_retry(monkeypatch):
    """Dispatcher caches the within-sweep pane_state, then the pane
    dies between gate-check and send. Initial send raises
    'target pane has exited'; within-sweep retry re-resolves to %new
    and the same reply lands on the live replacement pane."""
    head = _make_head(payload_ref='reply:rep-mid-sweep', evt_id='evt-mid-sweep')
    reply = _make_reply(reply_id='rep-mid-sweep', body='post-replacement')

    pane_alive = {'%old': True, '%new': True}

    class _LifecycleBackend(_MockTmuxBackend):
        def send_text_to_pane(self, pane_id, text, **kwargs):
            # First send to %old — pane has died mid-sweep — raise.
            if pane_id == '%old':
                pane_alive['%old'] = False
                raise RuntimeError('target pane has exited (pane_id=%old)')
            if not pane_alive.get(pane_id, False):
                raise RuntimeError(f'target pane has exited (pane_id={pane_id})')
            self.injected.append((pane_id, text))

    backend = _LifecycleBackend(pane_alive_map=pane_alive)
    dispatcher, kernel = _make_dispatcher(head=head, reply=reply)

    lookup_seq = ['%old', '%new']

    def _lookup(d, l):
        return lookup_seq.pop(0) if lookup_seq else '%new'

    monkeypatch.setattr(preparation_service, '_lookup_cmd_pane_id', _lookup)
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend)
    _patch_claude_foreground(monkeypatch)

    preparation_service._deliver_cmd_replies(dispatcher)

    assert len(backend.injected) == 1
    assert backend.injected[0][0] == '%new'
    assert kernel.calls == []


# --------------------------------------------------------------------------- #
# 5) Cache no-duplicate (existing protection preserved)
# --------------------------------------------------------------------------- #


def test_persisted_cache_prevents_duplicate_inject_after_simulated_restart(
    monkeypatch, tmp_path
):
    """Shape A/B do not weaken duplicate-injection protection. After a
    successful inject persists the reply id to disk, a brand-new
    dispatcher (simulating ccbd restart) reads the persisted cache on
    cold start and skips the re-inject."""
    head_a = _make_head(payload_ref='reply:rep-dup', evt_id='evt-dup')
    reply = _make_reply(reply_id='rep-dup', body='deliver once')
    backend_a = _MockTmuxBackend(pane_alive_map={'%1': True})
    dispatcher_a, kernel_a = _make_dispatcher(
        head=head_a, reply=reply, project_root=tmp_path,
        clock='2026-05-05T00:00:00Z',
    )
    monkeypatch.setattr(preparation_service, '_lookup_cmd_pane_id', lambda d, l: '%1')
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend_a)
    _patch_claude_foreground(monkeypatch)

    preparation_service._deliver_cmd_replies(dispatcher_a)
    assert len(backend_a.injected) == 1

    # Repeat sweeps in same dispatcher: no re-inject (in-memory cache).
    preparation_service._deliver_cmd_replies(dispatcher_a)
    preparation_service._deliver_cmd_replies(dispatcher_a)
    assert len(backend_a.injected) == 1

    # Simulate restart: brand-new dispatcher with same project_root.
    head_b = _make_head(payload_ref='reply:rep-dup', evt_id='evt-dup-2')
    backend_b = _MockTmuxBackend(pane_alive_map={'%1': True})
    dispatcher_b, kernel_b = _make_dispatcher(
        head=head_b, reply=reply, project_root=tmp_path,
        clock='2026-05-05T00:05:00Z',
    )
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend_b)
    preparation_service._deliver_cmd_replies(dispatcher_b)

    assert backend_b.injected == [], (
        'persisted cache must prevent duplicate inject across restart'
    )
    assert kernel_b.calls == []


def test_repeated_sweeps_after_successful_inject_do_not_re_inject(monkeypatch):
    """Once a reply has been injected and the K-counter cleared, repeated
    sweeps with the same pending head (still awaiting human ack) must
    not re-inject the reply text into the pane."""
    head = _make_head(payload_ref='reply:rep-once-only', evt_id='evt-once-only')
    reply = _make_reply(reply_id='rep-once-only', body='inject once')
    backend = _MockTmuxBackend(pane_alive_map={'%1': True})
    dispatcher, kernel = _make_dispatcher(head=head, reply=reply)

    monkeypatch.setattr(preparation_service, '_lookup_cmd_pane_id', lambda d, l: '%1')
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend)
    _patch_claude_foreground(monkeypatch)

    for _ in range(5):
        preparation_service._deliver_cmd_replies(dispatcher)

    assert len(backend.injected) == 1, 'sweeping repeatedly must not re-inject'
    assert kernel.calls == []


# --------------------------------------------------------------------------- #
# 6) Pruning of K-counter dict (longevity / leak protection)
# --------------------------------------------------------------------------- #


def test_retry_counter_prunes_entries_for_no_longer_pending_events(monkeypatch):
    """Stale K-counter entries for events no longer in `pending` must be
    pruned at sweep entry. Without pruning, a long-lived daemon could
    leak counter dict entries every time an event is consumed via a
    non-delivery path (e.g., direct ack)."""
    head = _make_head(payload_ref='reply:rep-prune', evt_id='evt-prune')
    reply = _make_reply(reply_id='rep-prune', body='prune')
    backend = _MockTmuxBackend(pane_alive_map={'%1': True})
    dispatcher, _kernel = _make_dispatcher(head=head, reply=reply)

    # Pre-load stale counters for events that are NOT in pending.
    dispatcher._cmd_pane_retry_counts = {
        'evt-stale-1': 2,
        'evt-stale-2': 1,
        head.inbound_event_id: 0,
    }

    monkeypatch.setattr(preparation_service, '_lookup_cmd_pane_id', lambda d, l: '%1')
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend)
    _patch_claude_foreground(monkeypatch)

    preparation_service._deliver_cmd_replies(dispatcher)

    counts = dispatcher._cmd_pane_retry_counts
    # Stale entries pruned; the still-pending entry was cleared on
    # successful delivery (so it ends up absent, not zero).
    assert 'evt-stale-1' not in counts
    assert 'evt-stale-2' not in counts
    assert head.inbound_event_id not in counts
