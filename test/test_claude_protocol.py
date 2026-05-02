from __future__ import annotations

from provider_backends.claude.protocol import extract_reply_for_req, wrap_claude_prompt, wrap_claude_turn_prompt


def test_extract_reply_for_req_uses_begin_and_done_window() -> None:
    text = (
        'CCB_BEGIN: job_old123\n'
        'old reply\n'
        'CCB_DONE: job_old123\n'
        '\n'
        'CCB_BEGIN: job_new123\n'
        'new reply line 1\n'
        'new reply line 2\n'
        'CCB_DONE: job_new123\n'
    )

    assert extract_reply_for_req(text, 'job_new123') == 'new reply line 1\nnew reply line 2'


def test_wrap_claude_prompt_does_not_inject_language_or_markdown_extras(monkeypatch) -> None:
    monkeypatch.setenv('CCB_REPLY_LANG', 'zh')

    prompt = wrap_claude_prompt('Please return a markdown table', 'req_1')

    assert 'Reply in Chinese.' not in prompt
    assert 'pipe-and-dash Markdown table syntax' not in prompt
    assert 'CCB_BEGIN: req_1' in prompt
    assert 'CCB_DONE: req_1' in prompt


def test_wrap_claude_turn_prompt_does_not_prefix_loaded_skills(monkeypatch) -> None:
    monkeypatch.delenv('CCB_REPLY_LANG', raising=False)
    monkeypatch.delenv('CCB_LANG', raising=False)

    prompt = wrap_claude_turn_prompt('hello', 'req_2')

    assert prompt.startswith('CCB_REQ_ID: req_2\n\nhello')
    assert 'SKILL BLOCK' not in prompt
    assert '# Async Ask' not in prompt


def test_wrap_claude_prompt_does_not_leak_runtime_skill_to_receiver() -> None:
    prompt = wrap_claude_prompt('hello user msg', 'req_3')

    assert 'hello user msg' in prompt
    assert '# Async Ask' not in prompt
    assert 'After successful async submit' not in prompt
    assert 'Use this only for' not in prompt
