from pathlib import Path

from mailbox_kernel import InboundEventRecord, InboundEventStatus, InboundEventStore, InboundEventType, MailboxKernelService
from storage.paths import PathLayout
from message_bureau.models import ReplyRecord, ReplyTerminalStatus
from message_bureau.store import ReplyStore
from storage import jsonl_store as jsonl_store_module


def _reply_event(event_id='evt-reply', reply_id='reply-1'):
    return InboundEventRecord(
        inbound_event_id=event_id,
        agent_name='cmd',
        event_type=InboundEventType.TASK_REPLY,
        message_id='msg-1',
        attempt_id='attempt-1',
        payload_ref=f'reply:{reply_id}',
        priority=10,
        status=InboundEventStatus.QUEUED,
        created_at='2026-04-30T00:00:00Z',
    )


def _reply(reply_id='reply-1'):
    return ReplyRecord(
        reply_id=reply_id,
        message_id='msg-1',
        attempt_id='attempt-1',
        agent_name='agent1',
        terminal_status=ReplyTerminalStatus.COMPLETED,
        reply='hello',
        diagnostics={},
        finished_at='2026-04-30T00:00:01Z',
    )


def test_cached_head_pending_and_reply_latest_do_not_parse_on_hits(monkeypatch, tmp_path):
    monkeypatch.setenv('CCB_CCBD_READAMP_CACHE', '1')
    layout = PathLayout(Path(tmp_path) / 'repo')
    inbound_store = InboundEventStore(layout)
    inbound_store.append(_reply_event())
    service = MailboxKernelService(layout, clock=lambda: '2026-04-30T00:00:00Z', inbound_store=inbound_store)
    reply_store = ReplyStore(layout)
    reply_store.append(_reply())

    calls = []
    real_loads = jsonl_store_module.json.loads

    def counted_loads(text):
        calls.append(text)
        return real_loads(text)

    monkeypatch.setattr(jsonl_store_module.json, 'loads', counted_loads)

    assert service.head_pending_event('cmd').inbound_event_id == 'evt-reply'
    assert reply_store.get_latest('reply-1').reply == 'hello'
    calls.clear()

    for _ in range(100):
        assert service.head_pending_event('cmd').inbound_event_id == 'evt-reply'
        assert reply_store.get_latest('reply-1').reply == 'hello'

    assert calls == []
