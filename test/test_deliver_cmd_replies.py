"""Direct tests for guarded cmd reply delivery.

The function's contract:
- It injects to safe, ready cmd consumers and leaves the head for human ack.
- Unsafe or not-ready consumers hold delivery without injecting or acking.
- Environmental failures (no pane, dead backend, send error) leave the head
  QUEUED so the next tick retries.
- Plan/send exceptions emit cmd_phase2_failure telemetry but don't burn the
  head.

These tests use SimpleNamespace fakes for dispatcher/kernel/control rather
than full integration scaffolding so the failure modes can be exercised
deterministically.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import logging

import pytest

from mailbox_kernel import InboundEventStatus, InboundEventType
from ccbd.services.dispatcher_runtime.reply_delivery_runtime import (
    cmd_delivery_telemetry,
    cmd_readiness_probes,
    preparation_service,
)


class _RecordingKernel:
    def __init__(self, head, *, ack_raises: int = 0):
        self._head = head
        self._ack_raises = ack_raises
        self.calls: list[tuple[str, str]] = []  # (method, inbound_event_id)

    def head_pending_event(self, agent_name: str):
        return self._head

    def claim(self, agent_name: str, inbound_event_id: str, *, started_at=None):
        self.calls.append(('claim', inbound_event_id))
        return self._head

    def consume(self, agent_name: str, inbound_event_id: str, *, finished_at=None):
        self.calls.append(('consume', inbound_event_id))
        return self._head

    def abandon(self, agent_name: str, inbound_event_id: str, *, finished_at=None):
        self.calls.append(('abandon', inbound_event_id))
        if self._head is not None and self._head.inbound_event_id == inbound_event_id:
            self._head.status = InboundEventStatus.ABANDONED
        return self._head

    def ack_reply(self, agent_name: str, inbound_event_id: str, *, started_at=None, finished_at=None):
        del agent_name, started_at, finished_at
        self.calls.append(('ack_reply', inbound_event_id))
        if self._ack_raises:
            self._ack_raises -= 1
            raise RuntimeError('ack failed')
        if self._head is not None and self._head.inbound_event_id == inbound_event_id:
            self._head.status = InboundEventStatus.CONSUMED
        return self._head


class _RecordingBackend:
    def __init__(
        self,
        *,
        alive: bool = True,
        send_raises: bool = False,
        pane_contents: list[str] | None = None,
        pane_content_raises: bool = False,
    ):
        self._alive = alive
        self._send_raises = send_raises
        self._pane_contents = list(pane_contents or ['❯ '])
        self._pane_content_raises = pane_content_raises
        self.injected: list[tuple[str, str]] = []

    def is_alive(self, pane_id: str) -> bool:
        return self._alive

    def send_text_to_pane(self, pane_id: str, text: str):
        if self._send_raises:
            raise RuntimeError('inject failed')
        self.injected.append((pane_id, text))

    def get_pane_content(self, pane_id: str, lines: int = 120) -> str:
        del pane_id, lines
        if self._pane_content_raises:
            raise RuntimeError('pane read failed')
        if not self._pane_contents:
            return ''
        if len(self._pane_contents) == 1:
            return self._pane_contents[0]
        return self._pane_contents.pop(0)


def _make_head(*, status=InboundEventStatus.QUEUED, payload_ref='reply:rep-1'):
    return SimpleNamespace(
        inbound_event_id='evt-1',
        event_type=InboundEventType.TASK_REPLY,
        status=status,
        payload_ref=payload_ref,
    )


def _make_reply(*, reply_id='rep-1', body='hello cmd'):
    return SimpleNamespace(
        attempt_id='att-1',
        agent_name='codex',
        reply_id=reply_id,
        terminal_status=SimpleNamespace(value='succeeded'),
        diagnostics={},
        reply=body,
    )


def _make_heartbeat_reply(*, reply_id='rep-1', body='CCB_HEARTBEAT from=agent1'):
    reply = _make_reply(reply_id=reply_id, body=body)
    reply.diagnostics = {'notice': True, 'notice_kind': 'heartbeat'}
    return reply


def _make_cancelled_empty_reply(*, reply_id='rep-1'):
    reply = _make_reply(reply_id=reply_id, body='')
    reply.terminal_status = SimpleNamespace(value='cancelled')
    return reply


def _make_dispatcher(
    *,
    head,
    reply,
    backend,
    pane_id='%1',
    project_root=None,
    job_id='job-1',
    task_id='task-1',
    clock='2026-04-23T00:00:00+00:00',
    ack_raises: int = 0,
):
    """Wire up a dispatcher SimpleNamespace with the minimum surface area
    that _deliver_cmd_replies and its helpers reach for."""
    kernel = _RecordingKernel(head, ack_raises=ack_raises)
    reply_store = SimpleNamespace(get_latest=lambda rid: reply if reply and reply.reply_id == rid else None)
    attempt_store = SimpleNamespace(get_latest=lambda aid: SimpleNamespace(job_id=job_id))
    control = SimpleNamespace(
        _mailbox_kernel=kernel,
        _reply_store=reply_store,
        _attempt_store=attempt_store,
    )
    layout = SimpleNamespace(project_root=project_root) if project_root is not None else None

    dispatcher = SimpleNamespace(
        _message_bureau_control=control,
        _layout=layout,
        _clock=lambda: clock,
        get_job=lambda jid: SimpleNamespace(job_id=job_id, request=SimpleNamespace(task_id=task_id)),
    )
    # Attach helpers to the dispatcher so the production code's
    # _discover_cmd_pane_id / _get_tmux_backend can be monkeypatched in
    # individual tests.
    return dispatcher, kernel


@pytest.fixture
def _stub_pane_and_backend(monkeypatch):
    """By default, return a working pane + backend. Tests override per-need."""
    backend = _RecordingBackend()
    monkeypatch.setattr(preparation_service, '_discover_cmd_pane_id', lambda d: '%1')
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend)
    monkeypatch.setattr(preparation_service, '_cmd_pane_foreground_command', lambda backend, pane_id: 'claude')
    return backend


def _cache_path(project_root: Path) -> Path:
    return project_root / '.ccb' / 'ccbd' / preparation_service._CMD_DELIVERED_CACHE_FILENAME


def _write_cache_records(project_root: Path, records: list[dict[str, str]]) -> Path:
    path = _cache_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as handle:
        for record in records:
            handle.write(json.dumps(record, separators=(',', ':')))
            handle.write('\n')
    return path


def _cache_lines(project_root: Path) -> list[str]:
    path = _cache_path(project_root)
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def _cache_record(reply_id: str, injected_at: str) -> dict[str, str]:
    return {'reply_id': reply_id, 'injected_at': injected_at}


def _read_metrics(project_root: Path) -> list[dict]:
    path = project_root / '.ccb' / 'metrics' / 'body_read_followup.jsonl'
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def _patch_claude_foreground(monkeypatch):
    monkeypatch.setattr(preparation_service, '_cmd_pane_foreground_command', lambda backend, pane_id: 'claude')


def _patch_foreground_sequence(monkeypatch, commands: list[str]):
    values = list(commands)

    def _next(_backend, _pane_id):
        if len(values) == 1:
            return values[0]
        return values.pop(0)

    monkeypatch.setattr(preparation_service, '_cmd_pane_foreground_command', _next)


# --- Happy path ---

def test_inject_sends_text_and_does_not_auto_ack_head(
    _stub_pane_and_backend, monkeypatch
):
    head = _make_head()
    reply = _make_reply(body='short reply text')
    dispatcher, kernel = _make_dispatcher(head=head, reply=reply, backend=_stub_pane_and_backend)

    preparation_service._deliver_cmd_replies(dispatcher)

    assert kernel.calls == []
    # The pane received the text.
    assert len(_stub_pane_and_backend.injected) == 1
    pane_id, text = _stub_pane_and_backend.injected[0]
    assert pane_id == '%1'
    assert 'short reply text' in text


def test_idempotent_no_reinject_on_second_call(_stub_pane_and_backend):
    head = _make_head()
    reply = _make_reply(body='one-shot text')
    dispatcher, kernel = _make_dispatcher(head=head, reply=reply, backend=_stub_pane_and_backend)

    preparation_service._deliver_cmd_replies(dispatcher)
    preparation_service._deliver_cmd_replies(dispatcher)
    preparation_service._deliver_cmd_replies(dispatcher)

    assert len(_stub_pane_and_backend.injected) == 1, 'should inject exactly once for the same reply_id'
    assert kernel.calls == []


# --- Persisted injected cache ---

def test_injected_cache_cold_start_missing_file_returns_empty(tmp_path):
    dispatcher, _kernel = _make_dispatcher(
        head=_make_head(),
        reply=_make_reply(),
        backend=None,
        project_root=tmp_path,
        clock='2026-04-24T00:00:00Z',
    )

    cache = preparation_service._get_injected_cache(dispatcher)

    assert list(cache.items()) == []
    assert not _cache_path(tmp_path).exists()


def test_injected_cache_cold_start_loads_existing_entries(tmp_path):
    _write_cache_records(
        tmp_path,
        [
            _cache_record('rep-1', '2026-04-23T22:00:00Z'),
            _cache_record('rep-2', '2026-04-23T22:10:00Z'),
            _cache_record('rep-3', '2026-04-23T22:20:00Z'),
        ],
    )
    dispatcher, _kernel = _make_dispatcher(
        head=_make_head(),
        reply=_make_reply(),
        backend=None,
        project_root=tmp_path,
        clock='2026-04-24T00:00:00Z',
    )

    cache = preparation_service._get_injected_cache(dispatcher)

    assert list(cache.keys()) == ['rep-1', 'rep-2', 'rep-3']


def test_injected_cache_cold_start_filters_expired_entries(tmp_path):
    _write_cache_records(
        tmp_path,
        [
            _cache_record('rep-expired', '2026-04-21T23:59:59Z'),
            _cache_record('rep-live', '2026-04-23T23:00:00Z'),
        ],
    )
    dispatcher, _kernel = _make_dispatcher(
        head=_make_head(),
        reply=_make_reply(),
        backend=None,
        project_root=tmp_path,
        clock='2026-04-24T00:00:00Z',
    )

    cache = preparation_service._get_injected_cache(dispatcher)

    assert list(cache.keys()) == ['rep-live']


def test_warm_restart_uses_persisted_cache_to_skip_reinject(monkeypatch, tmp_path):
    reply = _make_reply(reply_id='rep-warm', body='first inject')

    backend_a = _RecordingBackend()
    monkeypatch.setattr(preparation_service, '_discover_cmd_pane_id', lambda d: '%1')
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend_a)
    _patch_claude_foreground(monkeypatch)
    dispatcher_a, _kernel_a = _make_dispatcher(
        head=_make_head(payload_ref='reply:rep-warm'),
        reply=reply,
        backend=backend_a,
        project_root=tmp_path,
        clock='2026-04-24T00:00:00Z',
    )

    preparation_service._deliver_cmd_replies(dispatcher_a)
    assert len(backend_a.injected) == 1

    backend_b = _RecordingBackend()
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend_b)
    dispatcher_b, kernel_b = _make_dispatcher(
        head=_make_head(payload_ref='reply:rep-warm'),
        reply=reply,
        backend=backend_b,
        project_root=tmp_path,
        clock='2026-04-24T00:05:00Z',
    )

    preparation_service._deliver_cmd_replies(dispatcher_b)

    assert backend_b.injected == []
    assert kernel_b.calls == []


def test_injected_cache_corrupt_line_skips_with_warning(tmp_path, caplog):
    path = _cache_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"reply_id":"rep-1","injected_at":"2026-04-23T23:00:00Z"}\n'
        '{bad json\n'
        '{"reply_id":"rep-2","injected_at":"2026-04-23T23:10:00Z"}\n',
        encoding='utf-8',
    )
    dispatcher, _kernel = _make_dispatcher(
        head=_make_head(),
        reply=_make_reply(),
        backend=None,
        project_root=tmp_path,
        clock='2026-04-24T00:00:00Z',
    )

    with caplog.at_level(logging.WARNING):
        cache = preparation_service._get_injected_cache(dispatcher)

    assert list(cache.keys()) == ['rep-1', 'rep-2']
    assert 'cmd injected cache line decode failed' in caplog.text


def test_injected_cache_invalid_timestamp_skips_with_debug(tmp_path, caplog):
    _write_cache_records(
        tmp_path,
        [
            _cache_record('rep-bad', 'not-a-timestamp'),
            _cache_record('rep-good', '2026-04-23T23:00:00Z'),
        ],
    )
    dispatcher, _kernel = _make_dispatcher(
        head=_make_head(),
        reply=_make_reply(),
        backend=None,
        project_root=tmp_path,
        clock='2026-04-24T00:00:00Z',
    )

    with caplog.at_level(logging.DEBUG):
        cache = preparation_service._get_injected_cache(dispatcher)

    assert list(cache.keys()) == ['rep-good']
    assert 'cmd injected cache timestamp parse failed' in caplog.text


def test_injected_cache_concurrent_persist_merges_all_reply_ids(tmp_path):
    dispatcher, _kernel = _make_dispatcher(
        head=_make_head(),
        reply=_make_reply(),
        backend=None,
        project_root=tmp_path,
        clock='2026-04-24T00:00:00Z',
    )

    def _persist(reply_id: str) -> None:
        preparation_service._persist_injected_reply(
            dispatcher, reply_id, f'2026-04-24T00:00:{reply_id[-2:]}Z'
        )

    reply_ids = [f'rep-{index:02d}' for index in range(10)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(_persist, reply_ids))

    reloaded, _kernel = _make_dispatcher(
        head=_make_head(),
        reply=_make_reply(),
        backend=None,
        project_root=tmp_path,
        clock='2026-04-24T01:00:00Z',
    )
    cache = preparation_service._get_injected_cache(reloaded)

    assert set(cache.keys()) == set(reply_ids)


def test_injected_cache_compacts_on_load_when_file_exceeds_cap(tmp_path):
    base = datetime(2026, 4, 23, 0, 0, tzinfo=timezone.utc)
    records = [
        _cache_record(
            f'rep-{index:05d}',
            (base + timedelta(seconds=index)).isoformat().replace('+00:00', 'Z'),
        )
        for index in range(11_000)
    ]
    path = _write_cache_records(tmp_path, records)
    dispatcher, _kernel = _make_dispatcher(
        head=_make_head(),
        reply=_make_reply(),
        backend=None,
        project_root=tmp_path,
        clock='2026-04-24T00:00:00Z',
    )

    cache = preparation_service._get_injected_cache(dispatcher)

    assert len(cache) == 10_000
    assert next(iter(cache)) == 'rep-01000'
    assert list(cache.keys())[-1] == 'rep-10999'
    compacted_lines = [line for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    assert len(compacted_lines) == 10_000


def test_send_failure_does_not_persist_cache_file(monkeypatch, tmp_path):
    reply = _make_reply(reply_id='rep-send-fail')
    head = _make_head(payload_ref='reply:rep-send-fail')
    backend = _RecordingBackend(send_raises=True)
    monkeypatch.setattr(preparation_service, '_discover_cmd_pane_id', lambda d: '%1')
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend)
    _patch_claude_foreground(monkeypatch)
    dispatcher, _kernel = _make_dispatcher(
        head=head,
        reply=reply,
        backend=backend,
        project_root=tmp_path,
        clock='2026-04-24T00:00:00Z',
    )

    preparation_service._deliver_cmd_replies(dispatcher)

    assert _cache_lines(tmp_path) == []


def test_persist_failure_is_tolerated_and_keeps_in_memory_cache(monkeypatch, tmp_path, caplog):
    reply = _make_reply(reply_id='rep-persist-fail')
    head = _make_head(payload_ref='reply:rep-persist-fail')
    backend = _RecordingBackend()
    monkeypatch.setattr(preparation_service, '_discover_cmd_pane_id', lambda d: '%1')
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend)
    _patch_claude_foreground(monkeypatch)

    monkeypatch.setattr(preparation_service.os, 'fsync', lambda _fd: (_ for _ in ()).throw(PermissionError('read-only')))
    dispatcher, _kernel = _make_dispatcher(
        head=head,
        reply=reply,
        backend=backend,
        project_root=tmp_path,
        clock='2026-04-24T00:00:00Z',
    )

    with caplog.at_level(logging.DEBUG):
        preparation_service._deliver_cmd_replies(dispatcher)

    cache = preparation_service._get_injected_cache(dispatcher)
    assert len(backend.injected) == 1
    assert reply.reply_id in cache
    assert 'cmd injected cache persist failed' in caplog.text


def test_injected_cache_load_caps_to_most_recent_ten_thousand(tmp_path):
    base = datetime(2026, 4, 23, 0, 0, tzinfo=timezone.utc)
    records = [
        _cache_record(
            f'rep-{index:05d}',
            (base + timedelta(seconds=index)).isoformat().replace('+00:00', 'Z'),
        )
        for index in range(20_000)
    ]
    _write_cache_records(tmp_path, records)
    dispatcher, _kernel = _make_dispatcher(
        head=_make_head(),
        reply=_make_reply(),
        backend=None,
        project_root=tmp_path,
        clock='2026-04-24T12:00:00Z',
    )

    cache = preparation_service._get_injected_cache(dispatcher)

    assert len(cache) == 10_000
    assert next(iter(cache)) == 'rep-10000'
    assert list(cache.keys())[-1] == 'rep-19999'


# --- Permanent failure: malformed payload ---

def test_malformed_payload_abandons_head(_stub_pane_and_backend):
    # No 'reply:' prefix → reply_id_from_payload returns ''
    head = _make_head(payload_ref='not-a-reply-ref')
    reply = _make_reply()
    dispatcher, kernel = _make_dispatcher(head=head, reply=reply, backend=_stub_pane_and_backend)

    preparation_service._deliver_cmd_replies(dispatcher)

    assert kernel.calls == [('abandon', 'evt-1')], (
        f'malformed payload must be abandoned (true permanent failure), got {kernel.calls}'
    )
    assert _stub_pane_and_backend.injected == []


# --- Environmental failures: head must stay queued ---

def test_no_pane_silent_return_head_untouched(monkeypatch):
    head = _make_head()
    reply = _make_reply()
    backend = _RecordingBackend()
    monkeypatch.setattr(preparation_service, '_discover_cmd_pane_id', lambda d: None)
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend)
    dispatcher, kernel = _make_dispatcher(head=head, reply=reply, backend=backend)

    preparation_service._deliver_cmd_replies(dispatcher)

    assert kernel.calls == [], 'no pane → silent return, head must NOT be claimed/abandoned'
    assert backend.injected == []


def test_no_backend_silent_return_head_untouched(monkeypatch):
    head = _make_head()
    reply = _make_reply()
    monkeypatch.setattr(preparation_service, '_discover_cmd_pane_id', lambda d: '%1')
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: None)
    dispatcher, kernel = _make_dispatcher(head=head, reply=reply, backend=None)

    preparation_service._deliver_cmd_replies(dispatcher)

    assert kernel.calls == [], 'no backend → silent return'


def test_pane_not_alive_silent_return_head_untouched(monkeypatch):
    head = _make_head()
    reply = _make_reply()
    backend = _RecordingBackend(alive=False)
    monkeypatch.setattr(preparation_service, '_discover_cmd_pane_id', lambda d: '%1')
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend)
    dispatcher, kernel = _make_dispatcher(head=head, reply=reply, backend=backend)

    preparation_service._deliver_cmd_replies(dispatcher)

    assert kernel.calls == []
    assert backend.injected == []


def test_send_failure_does_not_claim_or_burn_head(monkeypatch):
    head = _make_head()
    reply = _make_reply()
    backend = _RecordingBackend(send_raises=True)
    monkeypatch.setattr(preparation_service, '_discover_cmd_pane_id', lambda d: '%1')
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend)
    _patch_claude_foreground(monkeypatch)
    dispatcher, kernel = _make_dispatcher(head=head, reply=reply, backend=backend)

    preparation_service._deliver_cmd_replies(dispatcher)

    # Head untouched — next tick retries when tmux recovers.
    assert kernel.calls == []
    # Cache must NOT contain the reply_id (we want retry).
    cache = preparation_service._get_injected_cache(dispatcher)
    assert reply.reply_id not in cache


def test_retry_succeeds_after_transient_send_failure(monkeypatch):
    """First tick: backend rejects send. Second tick: backend healthy. Reply
    delivered exactly once and head still untouched."""
    head = _make_head()
    reply = _make_reply()
    backend = _RecordingBackend(send_raises=True)
    monkeypatch.setattr(preparation_service, '_discover_cmd_pane_id', lambda d: '%1')
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend)
    _patch_claude_foreground(monkeypatch)
    dispatcher, kernel = _make_dispatcher(head=head, reply=reply, backend=backend)

    preparation_service._deliver_cmd_replies(dispatcher)
    assert backend.injected == []

    backend._send_raises = False
    preparation_service._deliver_cmd_replies(dispatcher)
    assert len(backend.injected) == 1
    assert kernel.calls == []


# --- DELIVERING / unexpected status filtering ---

def test_delivering_head_is_not_re_injected(_stub_pane_and_backend):
    head = _make_head(status=InboundEventStatus.DELIVERING)
    reply = _make_reply()
    dispatcher, kernel = _make_dispatcher(head=head, reply=reply, backend=_stub_pane_and_backend)

    preparation_service._deliver_cmd_replies(dispatcher)

    assert kernel.calls == []
    assert _stub_pane_and_backend.injected == []


def test_non_task_reply_event_ignored(_stub_pane_and_backend):
    head = _make_head()
    head.event_type = InboundEventType.TASK_REQUEST  # not a reply
    reply = _make_reply()
    dispatcher, kernel = _make_dispatcher(head=head, reply=reply, backend=_stub_pane_and_backend)

    preparation_service._deliver_cmd_replies(dispatcher)

    assert kernel.calls == []
    assert _stub_pane_and_backend.injected == []


# --- Telemetry ---

def test_phase2_failure_telemetry_on_send_exception(monkeypatch, tmp_path):
    head = _make_head()
    reply = _make_reply(body='x' * 10)
    backend = _RecordingBackend(send_raises=True)
    monkeypatch.setattr(preparation_service, '_discover_cmd_pane_id', lambda d: '%1')
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend)
    _patch_claude_foreground(monkeypatch)
    dispatcher, kernel = _make_dispatcher(head=head, reply=reply, backend=backend, project_root=tmp_path)

    preparation_service._deliver_cmd_replies(dispatcher)

    metrics_file = tmp_path / '.ccb' / 'metrics' / 'body_read_followup.jsonl'
    assert metrics_file.exists(), 'phase2 telemetry must be written'
    records = [json.loads(line) for line in metrics_file.read_text(encoding='utf-8').splitlines() if line.strip()]
    failure_records = [r for r in records if r.get('event') == 'cmd_phase2_failure']
    assert len(failure_records) == 1
    assert failure_records[0]['stage'] == 'send'
    assert failure_records[0]['reason'] == 'exception'
    assert failure_records[0]['reply_id'] == reply.reply_id


def test_long_body_happy_path_emits_header_only_dispatch_telemetry(
    monkeypatch, _stub_pane_and_backend, tmp_path
):
    """Explicit header-only mode injects only the single generated header."""
    from ccbd.services.dispatcher_runtime.reply_delivery_runtime.cmd_transport_planner import (
        CMD_HEADER_RE,
        _BODY_CHAR_THRESHOLD,
        resolve_cmd_delivery_mode,
    )
    monkeypatch.setenv('CCB_CMD_DELIVERY_MODE', 'header_only')
    monkeypatch.setenv('CCB_CMD_HEADER_ONLY_COMPATIBLE', '1')
    monkeypatch.delenv('CCB_HEADER_ONLY', raising=False)
    head = _make_head()
    long_body = 'w' * (_BODY_CHAR_THRESHOLD + 1)
    reply = _make_reply(body=long_body)
    dispatcher, kernel = _make_dispatcher(
        head=head, reply=reply, backend=_stub_pane_and_backend, project_root=tmp_path,
    )
    dispatcher._cmd_delivery_mode_result = resolve_cmd_delivery_mode(
        project_root=tmp_path,
        header_only_compatible=True,
    )

    preparation_service._deliver_cmd_replies(dispatcher)

    # Pane got the v8.3.2 generated header, NOT the full body.
    assert len(_stub_pane_and_backend.injected) == 1
    _, pane_text = _stub_pane_and_backend.injected[0]
    assert CMD_HEADER_RE.fullmatch(pane_text)
    assert long_body not in pane_text, 'long body must NOT be inlined into pane text'

    # Header injection telemetry recorded.
    metrics_file = tmp_path / '.ccb' / 'metrics' / 'body_read_followup.jsonl'
    assert metrics_file.exists()
    records = [json.loads(line) for line in metrics_file.read_text(encoding='utf-8').splitlines() if line.strip()]
    dispatch_events = [r for r in records if r.get('event') == 'cmd_delivery_header_inject_success']
    assert len(dispatch_events) == 1
    assert dispatch_events[0]['reply_id'] == reply.reply_id
    assert dispatch_events[0]['body_char_count'] == len(long_body)
    assert kernel.calls == []


def test_long_body_without_project_root_records_fallback_telemetry(monkeypatch, tmp_path):
    """Legacy full-body mode still injects body and records success telemetry."""
    from ccbd.services.dispatcher_runtime.reply_delivery_runtime.cmd_transport_planner import (
        _BODY_CHAR_THRESHOLD,
    )
    monkeypatch.setenv('CCB_HEADER_ONLY', '0')
    head = _make_head()
    long_body = 'f' * (_BODY_CHAR_THRESHOLD + 1)
    reply = _make_reply(body=long_body)
    backend = _RecordingBackend()
    monkeypatch.setattr(preparation_service, '_discover_cmd_pane_id', lambda d: '%1')
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend)
    _patch_claude_foreground(monkeypatch)
    dispatcher, kernel = _make_dispatcher(
        head=head, reply=reply, backend=backend, project_root=tmp_path,
    )

    preparation_service._deliver_cmd_replies(dispatcher)

    # Full body reached the pane (kill switch forced).
    assert len(backend.injected) == 1
    _, pane_text = backend.injected[0]
    assert long_body in pane_text

    # Full-body success telemetry is recorded; v8.3.2 no longer has a pointer fallback.
    metrics_file = tmp_path / '.ccb' / 'metrics' / 'body_read_followup.jsonl'
    assert metrics_file.exists()
    records = [json.loads(line) for line in metrics_file.read_text(encoding='utf-8').splitlines() if line.strip()]
    success_events = [r for r in records if r.get('event') == 'cmd_delivery_success']
    assert len(success_events) == 1
    assert success_events[0]['body_char_count'] == len(long_body)
    assert kernel.calls == []


def test_plan_exception_records_phase2_failure_telemetry(monkeypatch, tmp_path):
    """If plan_cmd_delivery itself raises (e.g., body_store write failed on
    disk-full), the planner telemetry event should fire so the failure is
    visible, and the head must NOT be burned."""
    from ccbd.services.dispatcher_runtime.reply_delivery_runtime import preparation_service as ps
    head = _make_head()
    reply = _make_reply(body='y' * 10)
    backend = _RecordingBackend()
    monkeypatch.setattr(ps, '_discover_cmd_pane_id', lambda d: '%1')
    monkeypatch.setattr(ps, '_get_tmux_backend', lambda d: backend)
    monkeypatch.setattr(ps, '_cmd_pane_foreground_command', lambda backend, pane_id: 'claude')

    def _boom(dispatcher, reply, *, project_root, body_store):
        raise RuntimeError('disk full')
    monkeypatch.setattr(ps, 'plan_cmd_delivery', _boom)

    dispatcher, kernel = _make_dispatcher(
        head=head, reply=reply, backend=backend, project_root=tmp_path,
    )

    ps._deliver_cmd_replies(dispatcher)

    # No inject, head untouched.
    assert backend.injected == []
    assert kernel.calls == []

    # Phase2 plan failure event recorded.
    metrics_file = tmp_path / '.ccb' / 'metrics' / 'body_read_followup.jsonl'
    assert metrics_file.exists()
    records = [json.loads(line) for line in metrics_file.read_text(encoding='utf-8').splitlines() if line.strip()]
    plan_failures = [
        r for r in records
        if r.get('event') == 'cmd_phase2_failure' and r.get('stage') == 'plan'
    ]
    assert len(plan_failures) == 1
    assert plan_failures[0]['reason'] == 'exception'
    assert plan_failures[0]['reply_id'] == reply.reply_id

    # Not cached — next tick retries.
    cache = ps._get_injected_cache(dispatcher)
    assert reply.reply_id not in cache


def test_lru_cache_eviction_keeps_user_visible_delivery_at_least_once(_stub_pane_and_backend):
    """cmd reply delivery is human-ack and at-least-once if the in-memory
    suppression cache is manually evicted while the mailbox head remains
    queued."""
    head = _make_head()
    reply = _make_reply()
    dispatcher, kernel = _make_dispatcher(head=head, reply=reply, backend=_stub_pane_and_backend)

    preparation_service._deliver_cmd_replies(dispatcher)
    assert len(_stub_pane_and_backend.injected) == 1

    # Manually evict the cache to simulate aging out.
    cache = preparation_service._get_injected_cache(dispatcher)
    cache.clear()

    preparation_service._deliver_cmd_replies(dispatcher)
    assert len(_stub_pane_and_backend.injected) == 2
    assert kernel.calls == []


def test_load_cmd_safe_consumers_default_without_project_root(monkeypatch):
    monkeypatch.delenv('CCB_CMD_SAFE_CONSUMERS', raising=False)

    assert preparation_service._load_cmd_safe_consumers(None) == preparation_service.DEFAULT_CMD_SAFE_CONSUMERS


def test_load_cmd_safe_consumers_env_overrides_project(monkeypatch, tmp_path):
    monkeypatch.setenv('CCB_CMD_SAFE_CONSUMERS', 'claude,aider')
    config_dir = tmp_path / '.ccb'
    config_dir.mkdir()
    (config_dir / 'cmd-safe-consumers.txt').write_text('codex\n', encoding='utf-8')

    assert preparation_service._load_cmd_safe_consumers(tmp_path) == frozenset({'claude', 'aider'})


def test_load_cmd_safe_consumers_reads_project_file(monkeypatch, tmp_path):
    monkeypatch.delenv('CCB_CMD_SAFE_CONSUMERS', raising=False)
    config_dir = tmp_path / '.ccb'
    config_dir.mkdir()
    (config_dir / 'cmd-safe-consumers.txt').write_text('# comment\nclaude\n  aider  \n', encoding='utf-8')

    assert preparation_service._load_cmd_safe_consumers(tmp_path) == frozenset({'claude', 'aider'})


def test_unsafe_foreground_holds_without_inject_or_ack(monkeypatch, tmp_path):
    head = _make_head()
    reply = _make_reply(body='do not inject into shell')
    backend = _RecordingBackend()
    monkeypatch.setattr(preparation_service, '_discover_cmd_pane_id', lambda d: '%1')
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend)
    monkeypatch.setattr(preparation_service, '_cmd_pane_foreground_command', lambda backend, pane_id: 'bash')
    dispatcher, kernel = _make_dispatcher(head=head, reply=reply, backend=backend, project_root=tmp_path)

    preparation_service._deliver_cmd_replies(dispatcher)

    assert backend.injected == []
    assert kernel.calls == []
    held = [r for r in _read_metrics(tmp_path) if r.get('event') == 'cmd_delivery_held']
    assert len(held) == 1
    assert held[0]['foreground_command'] == 'bash'
    assert held[0]['held_reason'] == 'not_safe_consumer'


def test_ready_claude_delivery_records_success(monkeypatch, tmp_path):
    head = _make_head()
    reply = _make_reply(body='safe to inject')
    backend = _RecordingBackend(pane_contents=['❯ ', '❯ '])
    monkeypatch.setattr(preparation_service, '_discover_cmd_pane_id', lambda d: '%1')
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend)
    _patch_claude_foreground(monkeypatch)
    dispatcher, kernel = _make_dispatcher(head=head, reply=reply, backend=backend, project_root=tmp_path)

    preparation_service._deliver_cmd_replies(dispatcher)

    assert len(backend.injected) == 1
    assert kernel.calls == []
    success = [r for r in _read_metrics(tmp_path) if r.get('event') == 'cmd_delivery_success']
    assert len(success) == 1
    assert success[0]['foreground_command'] == 'claude'


def test_heartbeat_notice_auto_acks_without_pane_lookup(monkeypatch, tmp_path):
    head = _make_head()
    reply = _make_heartbeat_reply()
    backend = _RecordingBackend()
    monkeypatch.setattr(preparation_service, '_discover_cmd_pane_id', lambda d: pytest.fail('heartbeat must not inspect pane'))
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend)
    dispatcher, kernel = _make_dispatcher(head=head, reply=reply, backend=backend, project_root=tmp_path)

    preparation_service._deliver_cmd_replies(dispatcher)

    assert backend.injected == []
    assert kernel.calls == [('ack_reply', 'evt-1')]
    assert _read_metrics(tmp_path) == []


def test_empty_cancelled_reply_auto_acks_without_pane_lookup(monkeypatch, tmp_path):
    head = _make_head()
    reply = _make_cancelled_empty_reply()
    backend = _RecordingBackend()
    monkeypatch.setattr(preparation_service, '_discover_cmd_pane_id', lambda d: pytest.fail('cancelled empty must not inspect pane'))
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend)
    dispatcher, kernel = _make_dispatcher(head=head, reply=reply, backend=backend, project_root=tmp_path)

    preparation_service._deliver_cmd_replies(dispatcher)

    assert backend.injected == []
    assert kernel.calls == [('ack_reply', 'evt-1')]
    assert _read_metrics(tmp_path) == []


def test_not_ready_claude_holds_without_inject_or_ack(monkeypatch, tmp_path):
    head = _make_head()
    reply = _make_reply(body='hold while typed')
    backend = _RecordingBackend(pane_contents=['❯ partial'])
    monkeypatch.setattr(preparation_service, '_discover_cmd_pane_id', lambda d: '%1')
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend)
    _patch_claude_foreground(monkeypatch)
    dispatcher, kernel = _make_dispatcher(head=head, reply=reply, backend=backend, project_root=tmp_path)

    preparation_service._deliver_cmd_replies(dispatcher)

    assert backend.injected == []
    assert kernel.calls == []
    held = [r for r in _read_metrics(tmp_path) if r.get('event') == 'cmd_delivery_held']
    assert len(held) == 1
    assert held[0]['held_reason'] == 'not_ready'


def test_probe_unavailable_holds_without_inject_or_ack(monkeypatch, tmp_path):
    head = _make_head()
    reply = _make_reply(body='hold when capture fails')
    backend = _RecordingBackend(pane_content_raises=True)
    monkeypatch.setattr(preparation_service, '_discover_cmd_pane_id', lambda d: '%1')
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend)
    _patch_claude_foreground(monkeypatch)
    dispatcher, kernel = _make_dispatcher(head=head, reply=reply, backend=backend, project_root=tmp_path)

    preparation_service._deliver_cmd_replies(dispatcher)

    assert backend.injected == []
    assert kernel.calls == []
    held = [r for r in _read_metrics(tmp_path) if r.get('event') == 'cmd_delivery_held']
    assert len(held) == 1
    assert held[0]['held_reason'] == 'probe_unavailable'


def test_safe_consumer_without_readiness_probe_holds_without_inject_or_ack(monkeypatch, tmp_path):
    head = _make_head()
    reply = _make_reply(body='do not inject into unprobed shell')
    backend = _RecordingBackend()
    monkeypatch.setenv('CCB_CMD_SAFE_CONSUMERS', 'bash')
    monkeypatch.setattr(preparation_service, '_discover_cmd_pane_id', lambda d: '%1')
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend)
    monkeypatch.setattr(preparation_service, '_cmd_pane_foreground_command', lambda backend, pane_id: 'bash')
    dispatcher, kernel = _make_dispatcher(head=head, reply=reply, backend=backend, project_root=tmp_path)

    preparation_service._deliver_cmd_replies(dispatcher)

    assert backend.injected == []
    assert kernel.calls == []
    held = [r for r in _read_metrics(tmp_path) if r.get('event') == 'cmd_delivery_held']
    assert len(held) == 1
    assert held[0]['foreground_command'] == 'bash'
    assert held[0]['held_reason'] == 'unknown_consumer'


def test_readiness_requires_identical_stable_bottom_window():
    backend = _RecordingBackend(
        pane_contents=[
            'ready scrollback\nType your message',
            'changed scrollback\nType your message',
        ]
    )

    result = preparation_service._cmd_pane_readiness(backend, '%1', 'claude')

    assert result == cmd_readiness_probes.ReadinessOutcome.NOT_READY


def test_ready_then_typed_prompt_before_send_holds(monkeypatch, tmp_path):
    head = _make_head()
    reply = _make_reply(body='race body')
    backend = _RecordingBackend(pane_contents=['❯ ', '❯ partial'])
    monkeypatch.setattr(preparation_service, '_discover_cmd_pane_id', lambda d: '%1')
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend)
    _patch_claude_foreground(monkeypatch)
    dispatcher, kernel = _make_dispatcher(head=head, reply=reply, backend=backend, project_root=tmp_path)

    preparation_service._deliver_cmd_replies(dispatcher)

    assert backend.injected == []
    assert kernel.calls == []
    held = [r for r in _read_metrics(tmp_path) if r.get('event') == 'cmd_delivery_held']
    assert len(held) == 1
    assert held[0]['held_reason'] == 'not_ready'


def test_foreground_changes_from_claude_to_bash_before_send_holds(monkeypatch, tmp_path):
    head = _make_head()
    reply = _make_reply(body='foreground race body')
    backend = _RecordingBackend(pane_contents=['❯ ', '❯ '])
    monkeypatch.setattr(preparation_service, '_discover_cmd_pane_id', lambda d: '%1')
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend)
    _patch_foreground_sequence(monkeypatch, ['claude', 'bash'])
    dispatcher, kernel = _make_dispatcher(head=head, reply=reply, backend=backend, project_root=tmp_path)

    preparation_service._deliver_cmd_replies(dispatcher)

    assert backend.injected == []
    assert kernel.calls == []
    held = [r for r in _read_metrics(tmp_path) if r.get('event') == 'cmd_delivery_held']
    assert len(held) == 1
    assert held[0]['foreground_command'] == 'bash'
    assert held[0]['held_reason'] == 'not_safe_consumer'


def test_long_body_rechecks_readiness_before_send(monkeypatch, tmp_path):
    from ccbd.services.dispatcher_runtime.reply_delivery_runtime.cmd_transport_planner import (
        _BODY_CHAR_THRESHOLD,
    )
    head = _make_head()
    reply = _make_reply(body='r' * (_BODY_CHAR_THRESHOLD + 1))
    backend = _RecordingBackend(pane_contents=['❯ ', 'Loading...'])
    monkeypatch.setattr(preparation_service, '_discover_cmd_pane_id', lambda d: '%1')
    monkeypatch.setattr(preparation_service, '_get_tmux_backend', lambda d: backend)
    _patch_claude_foreground(monkeypatch)
    dispatcher, kernel = _make_dispatcher(head=head, reply=reply, backend=backend, project_root=tmp_path)

    preparation_service._deliver_cmd_replies(dispatcher)

    assert backend.injected == []
    assert kernel.calls == []
    held = [r for r in _read_metrics(tmp_path) if r.get('event') == 'cmd_delivery_held']
    assert len(held) == 1
    assert held[0]['held_reason'] == 'not_ready'
