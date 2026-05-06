from __future__ import annotations

from provider_backends.codex.comm_runtime.log_entries import extract_entry, extract_event, extract_message, extract_user_message


def test_extract_message_from_response_item_joins_assistant_content() -> None:
    entry = {
        'type': 'response_item',
        'payload': {
            'type': 'message',
            'role': 'assistant',
            'content': [
                {'type': 'output_text', 'text': 'hello'},
                {'type': 'text', 'text': 'world'},
            ],
        },
    }

    assert extract_message(entry) == 'hello\nworld'


def test_extract_user_message_from_response_item_input_text() -> None:
    entry = {
        'type': 'response_item',
        'payload': {
            'type': 'message',
            'role': 'user',
            'content': [
                {'type': 'input_text', 'text': 'first'},
                {'type': 'input_text', 'text': 'second'},
            ],
        },
    }

    assert extract_user_message(entry) == 'first\nsecond'
    assert extract_entry(entry) == {
        'entry_type': 'response_item',
        'payload_type': 'message',
        'timestamp': None,
        'phase': None,
        'turn_id': None,
        'reason': None,
        'last_agent_message': None,
        'entry': entry,
        'role': 'user',
        'text': 'first\nsecond',
    }


def test_extract_entry_handles_system_event_payloads() -> None:
    task_complete = {
        'type': 'event_msg',
        'payload': {
            'type': 'task_complete',
            'last_agent_message': 'done',
            'reason': 'completed',
        },
    }
    turn_aborted = {
        'type': 'event_msg',
        'payload': {
            'type': 'turn_aborted',
            'message': 'stopped',
        },
    }

    assert extract_entry(task_complete)['role'] == 'system'
    assert extract_entry(task_complete)['text'] == 'done'
    assert extract_entry(turn_aborted)['reason'] == 'turn_aborted'


def test_extract_entry_preserves_top_level_turn_id() -> None:
    entry = {
        'type': 'event_msg',
        'turn_id': 'turn-top-level',
        'payload': {
            'type': 'task_complete',
            'last_agent_message': 'done',
        },
    }

    normalized = extract_entry(entry)

    assert normalized is not None
    assert normalized['turn_id'] == 'turn-top-level'


def test_extract_entry_preserves_nested_turn_id() -> None:
    entry = {
        'type': 'event_msg',
        'payload': {
            'type': 'task_complete',
            'turn_id': 'turn-nested',
            'last_agent_message': 'done',
        },
    }

    normalized = extract_entry(entry)

    assert normalized is not None
    assert normalized['turn_id'] == 'turn-nested'


def test_extract_entry_preserves_task_started_turn_metadata() -> None:
    entry = {
        'type': 'event_msg',
        'payload': {
            'type': 'task_started',
            'turn_id': 'turn-started',
        },
    }

    normalized = extract_entry(entry)

    assert normalized is not None
    assert normalized['role'] == 'meta'
    assert normalized['payload_type'] == 'task_started'
    assert normalized['turn_id'] == 'turn-started'


def test_extract_entry_preserves_turn_context_metadata() -> None:
    entry = {
        'type': 'turn_context',
        'payload': {
            'turn_id': 'turn-context',
        },
    }

    normalized = extract_entry(entry)

    assert normalized is not None
    assert normalized['role'] == 'meta'
    assert normalized['entry_type'] == 'turn_context'
    assert normalized['payload_type'] == 'turn_context'
    assert normalized['turn_id'] == 'turn-context'


def test_extract_entry_prefers_top_level_turn_id_for_mixed_records() -> None:
    entry = {
        'type': 'event_msg',
        'turn_id': 'turn-top-level',
        'payload': {
            'type': 'task_complete',
            'turn_id': 'turn-nested',
            'last_agent_message': 'done',
        },
    }

    normalized = extract_entry(entry)

    assert normalized is not None
    assert normalized['turn_id'] == 'turn-top-level'


def test_extract_event_returns_only_user_or_assistant_messages() -> None:
    entry = {
        'type': 'event_msg',
        'payload': {
            'type': 'assistant_message',
            'role': 'assistant',
            'message': 'reply',
        },
    }

    assert extract_event(entry) == ('assistant', 'reply')
