from __future__ import annotations

import inspect


def send_prompt_to_runtime_target(
    backend: object,
    pane_id: str,
    text: str,
    *,
    extra_enter: bool = False,
) -> None:
    strict_send = getattr(backend, 'send_text_to_pane', None)
    if callable(strict_send):
        if extra_enter and _accepts_extra_enter(strict_send):
            strict_send(pane_id, text, extra_enter=True)
        else:
            strict_send(pane_id, text)
        return
    send_text = getattr(backend, 'send_text', None)
    if callable(send_text):
        if extra_enter and _accepts_extra_enter(send_text):
            send_text(pane_id, text, extra_enter=True)
        else:
            send_text(pane_id, text)
        return
    raise RuntimeError('terminal backend does not support text submission')


def _accepts_extra_enter(method: object) -> bool:
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return True
    return (
        'extra_enter' in signature.parameters
        or any(param.kind is inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values())
    )


def is_runtime_target_alive(backend: object, pane_id: str) -> bool:
    strict_check = getattr(backend, 'is_tmux_pane_alive', None)
    if callable(strict_check):
        return bool(strict_check(pane_id))
    is_alive = getattr(backend, 'is_alive', None)
    if callable(is_alive):
        return bool(is_alive(pane_id))
    return False


__all__ = ['is_runtime_target_alive', 'send_prompt_to_runtime_target']
