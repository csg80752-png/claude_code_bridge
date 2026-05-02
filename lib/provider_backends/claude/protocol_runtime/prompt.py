from __future__ import annotations

from provider_core.protocol import BEGIN_PREFIX, DONE_PREFIX, REQ_ID_PREFIX


def wrap_claude_prompt(message: str, req_id: str) -> str:
    body = _build_prompt_body(message)
    return (
        f'{REQ_ID_PREFIX} {req_id}\n\n'
        f'{body}'
        'Reply using exactly this format:\n'
        f'{BEGIN_PREFIX} {req_id}\n'
        '<reply>\n'
        f'{DONE_PREFIX} {req_id}\n'
    )


def wrap_claude_turn_prompt(message: str, req_id: str) -> str:
    body = _build_prompt_body(message)
    return f'{REQ_ID_PREFIX} {req_id}\n\n{body}'


def _build_prompt_body(message: str) -> str:
    rendered = (message or '').rstrip()
    return f'{rendered}\n\n'


__all__ = ['wrap_claude_prompt', 'wrap_claude_turn_prompt']
