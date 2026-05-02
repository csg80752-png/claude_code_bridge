from pathlib import Path
from types import SimpleNamespace

from ccbd.services.dispatcher_runtime.reply_delivery_runtime import preparation_service as cmd_replies
from mailbox_kernel import InboundEventRecord, InboundEventStatus, InboundEventStore, InboundEventType, MailboxKernelService
from storage.paths import PathLayout
from message_bureau.models import ReplyRecord, ReplyTerminalStatus
from message_bureau.store import ReplyStore


class _Backend:
    def __init__(self):
        self.sent = []

    def is_alive(self, pane_id):
        return True

    def send_text_to_pane(self, pane_id, text):
        self.sent.append((pane_id, text))


def _reply_event(reply_id):
    return InboundEventRecord(
        inbound_event_id=f'evt-{reply_id}',
        agent_name='cmd',
        event_type=InboundEventType.TASK_REPLY,
        message_id='msg-1',
        attempt_id='attempt-1',
        payload_ref=f'reply:{reply_id}',
        priority=10,
        status=InboundEventStatus.QUEUED,
        created_at='2026-04-30T00:00:00Z',
    )


def _reply(reply_id):
    return ReplyRecord(
        reply_id=reply_id,
        message_id='msg-1',
        attempt_id='attempt-1',
        agent_name='agent1',
        terminal_status=ReplyTerminalStatus.COMPLETED,
        reply='hello from cache',
        diagnostics={},
        finished_at='2026-04-30T00:00:01Z',
    )


def test_deliver_cmd_replies_sees_reply_appended_after_cached_empty_head(monkeypatch, tmp_path):
    monkeypatch.setenv('CCB_CCBD_READAMP_CACHE', '1')
    monkeypatch.setenv('CCB_CMD_READY_GATE', '0')
    layout = PathLayout(Path(tmp_path) / 'repo')
    inbox = layout.agent_inbox_path('cmd')
    inbox.parent.mkdir(parents=True, exist_ok=True)
    inbox.touch()

    inbound_store = InboundEventStore(layout)
    kernel = MailboxKernelService(layout, clock=lambda: '2026-04-30T00:00:00Z', inbound_store=inbound_store)
    reply_store = ReplyStore(layout)
    assert kernel.head_pending_event('cmd') is None

    inbound_store.append(_reply_event('reply-1'))
    reply_store.append(_reply('reply-1'))

    backend = _Backend()
    monkeypatch.setattr(cmd_replies, '_discover_cmd_pane_id', lambda dispatcher: 'cmd-pane')
    monkeypatch.setattr(cmd_replies, '_get_tmux_backend', lambda dispatcher: backend)
    monkeypatch.setattr(cmd_replies, '_cmd_pane_foreground_command', lambda backend, pane_id: 'claude')
    monkeypatch.setattr(
        cmd_replies,
        'plan_cmd_delivery',
        lambda dispatcher, reply, project_root, body_store: (
            SimpleNamespace(body=reply.reply, header_only=False, body_file=None),
            None,
        ),
    )

    dispatcher = SimpleNamespace(
        _message_bureau_control=SimpleNamespace(_mailbox_kernel=kernel, _reply_store=reply_store),
        _layout=SimpleNamespace(project_root=Path(tmp_path)),
        _clock=lambda: '2026-04-30T00:00:02Z',
    )

    cmd_replies._deliver_cmd_replies(dispatcher)

    assert len(backend.sent) == 1
    assert backend.sent[0][0] == 'cmd-pane'
    assert 'hello from cache' in backend.sent[0][1]
    assert inbound_store.get_latest('cmd', 'evt-reply-1').status == InboundEventStatus.QUEUED
