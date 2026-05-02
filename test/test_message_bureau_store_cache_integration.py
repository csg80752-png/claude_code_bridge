from pathlib import Path

from message_bureau.models import AttemptRecord, AttemptState, MessageRecord, MessageState, ReplyRecord, ReplyTerminalStatus
from message_bureau.store import AttemptStore, MessageStore, ReplyStore
from storage import jsonl_store as jsonl_store_module
from storage.paths import PathLayout


def _message(message_id):
    return MessageRecord(
        message_id=message_id,
        origin_message_id=None,
        from_actor='cmd',
        target_scope='single',
        target_agents=('agent1',),
        message_class='task_request',
        reply_policy={'mode': 'one'},
        retry_policy={'mode': 'manual'},
        priority=100,
        payload_ref='payload://msg',
        submission_id='sub-1',
        created_at='2026-04-30T00:00:00Z',
        updated_at='2026-04-30T00:00:00Z',
        message_state=MessageState.CREATED,
    )


def _attempt(attempt_id, message_id='msg-1'):
    return AttemptRecord(
        attempt_id=attempt_id,
        message_id=message_id,
        agent_name='agent1',
        provider='codex',
        job_id=f'job-{attempt_id}',
        retry_index=0,
        health_snapshot_ref=None,
        started_at='2026-04-30T00:00:00Z',
        updated_at='2026-04-30T00:00:00Z',
        attempt_state=AttemptState.RUNNING,
    )


def _reply(reply_id, message_id='msg-1', attempt_id='attempt-1'):
    return ReplyRecord(
        reply_id=reply_id,
        message_id=message_id,
        attempt_id=attempt_id,
        agent_name='agent1',
        terminal_status=ReplyTerminalStatus.COMPLETED,
        reply='done',
        diagnostics={},
        finished_at='2026-04-30T00:00:01Z',
    )


def test_message_attempt_reply_stores_use_cache(monkeypatch, tmp_path):
    monkeypatch.setenv('CCB_CCBD_READAMP_CACHE', '1')
    layout = PathLayout(Path(tmp_path) / 'repo')
    messages = MessageStore(layout)
    attempts = AttemptStore(layout)
    replies = ReplyStore(layout)
    messages.append(_message('msg-1'))
    attempts.append(_attempt('attempt-1'))
    replies.append(_reply('reply-1'))

    calls = []
    real_loads = jsonl_store_module.json.loads

    def counted_loads(text):
        calls.append(text)
        return real_loads(text)

    monkeypatch.setattr(jsonl_store_module.json, 'loads', counted_loads)

    assert messages.list_all()[0].message_id == 'msg-1'
    assert attempts.list_all()[0].attempt_id == 'attempt-1'
    assert replies.list_all()[0].reply_id == 'reply-1'
    assert len(calls) == 3

    messages.list_all()
    attempts.list_all()
    replies.list_all()
    assert len(calls) == 3

    replies.append(_reply('reply-2'))
    assert [reply.reply_id for reply in replies.list_all()] == ['reply-1', 'reply-2']
    assert len(calls) == 4
