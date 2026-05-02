from pathlib import Path

from mailbox_kernel import InboundEventRecord, InboundEventStatus, InboundEventStore, InboundEventType
from storage.paths import PathLayout
from storage import jsonl_store as jsonl_store_module


def _event(event_id, agent='agent1'):
    return InboundEventRecord(
        inbound_event_id=event_id,
        agent_name=agent,
        event_type=InboundEventType.TASK_REPLY,
        message_id=f'msg-{event_id}',
        attempt_id=f'attempt-{event_id}',
        payload_ref=f'reply:{event_id}',
        priority=10,
        status=InboundEventStatus.QUEUED,
        created_at='2026-04-30T00:00:00Z',
    )


def test_inbound_event_store_list_agent_uses_incremental_cache(monkeypatch, tmp_path):
    monkeypatch.setenv('CCB_CCBD_READAMP_CACHE', '1')
    store = InboundEventStore(PathLayout(Path(tmp_path) / 'repo'))
    store.append(_event('a'))
    store.append(_event('b'))

    calls = []
    real_loads = jsonl_store_module.json.loads

    def counted_loads(text):
        calls.append(text)
        return real_loads(text)

    monkeypatch.setattr(jsonl_store_module.json, 'loads', counted_loads)

    first = store.list_agent('agent1')
    assert [row.inbound_event_id for row in first] == ['a', 'b']
    assert len(calls) == 2

    second = store.list_agent('agent1')
    assert second is first
    assert len(calls) == 2

    store.append(_event('c'))
    third = store.list_agent('agent1')
    assert third is first
    assert [row.inbound_event_id for row in third] == ['a', 'b', 'c']
    assert len(calls) == 3
