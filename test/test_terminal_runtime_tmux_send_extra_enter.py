"""Tests for TmuxTextSender's extra_enter kwarg (claude paste-collapse fix).

claude CLI bracketed paste needs a 2nd Enter to actually submit the prompt.
extra_enter=True triggers a delayed second Enter sent as a separate
send-keys after the existing paste compound.
"""

from __future__ import annotations

import subprocess

import pytest

from terminal_runtime.tmux_send import TmuxTextSender


def _cp(*, stdout: str = '', returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=['tmux'], returncode=returncode, stdout=stdout, stderr='')


def _make_sender(calls: list[list[str]], sleeps: list[float], *, env_delay: float = 0.5):
    return TmuxTextSender(
        tmux_run_fn=lambda args, **kwargs: calls.append(args) or _cp(),
        looks_like_tmux_target_fn=lambda value: True,
        ensure_not_in_copy_mode_fn=lambda pane_id: None,
        build_buffer_name_fn=lambda **kwargs: 'buf-extra',
        sanitize_text_fn=lambda text: text,
        should_use_inline_legacy_send_fn=lambda **kwargs: False,
        env_float_fn=lambda name, default: env_delay if name == 'CCB_TMUX_ENTER_DELAY' else default,
        sleep_fn=lambda seconds: sleeps.append(seconds),
    )


def test_extra_enter_true_sends_second_enter_after_delay_for_pane_target() -> None:
    """extra_enter=True on a tmux pane target: paste compound + delay + second Enter + delete-buffer."""
    calls: list[list[str]] = []
    sleeps: list[float] = []
    sender = _make_sender(calls, sleeps, env_delay=0.7)

    sender.send_text('%1', 'hello', extra_enter=True)

    # Expected sequence (in order):
    #   1. load-buffer
    #   2. paste-buffer ... ; send-keys ... Enter (compound, has 1 Enter inside)
    #   3. send-keys ... Enter (the SECOND Enter, after sleep)
    #   4. delete-buffer (finally)
    assert calls == [
        ['load-buffer', '-b', 'buf-extra', '-'],
        ['paste-buffer', '-p', '-t', '%1', '-b', 'buf-extra', ';', 'send-keys', '-t', '%1', 'Enter'],
        ['send-keys', '-t', '%1', 'Enter'],
        ['delete-buffer', '-b', 'buf-extra'],
    ]
    # Sleep happened exactly once with the env_float-resolved delay.
    assert sleeps == [0.7]


def test_extra_enter_true_uses_env_delay_default_when_unset() -> None:
    """env_float returns default 0.5s when CCB_TMUX_ENTER_DELAY is unset; sleep matches."""
    calls: list[list[str]] = []
    sleeps: list[float] = []
    sender = TmuxTextSender(
        tmux_run_fn=lambda args, **kwargs: calls.append(args) or _cp(),
        looks_like_tmux_target_fn=lambda value: True,
        ensure_not_in_copy_mode_fn=lambda pane_id: None,
        build_buffer_name_fn=lambda **kwargs: 'buf-default-delay',
        sanitize_text_fn=lambda text: text,
        should_use_inline_legacy_send_fn=lambda **kwargs: False,
        env_float_fn=lambda name, default: default,
        sleep_fn=lambda seconds: sleeps.append(seconds),
    )

    sender.send_text('%2', 'hi', extra_enter=True)

    assert sleeps == [0.5]


def test_extra_enter_true_for_session_target_inline_legacy_path() -> None:
    """extra_enter=True via the inline-legacy session path also sends a second Enter after delay."""
    calls: list[list[str]] = []
    sleeps: list[float] = []
    sender = TmuxTextSender(
        tmux_run_fn=lambda args, **kwargs: calls.append(args) or _cp(),
        looks_like_tmux_target_fn=lambda value: False,
        ensure_not_in_copy_mode_fn=lambda pane_id: None,
        build_buffer_name_fn=lambda **kwargs: 'buf-legacy',
        sanitize_text_fn=lambda text: text,
        should_use_inline_legacy_send_fn=lambda **kwargs: True,
        env_float_fn=lambda name, default: 0.3 if name == 'CCB_TMUX_ENTER_DELAY' else default,
        sleep_fn=lambda seconds: sleeps.append(seconds),
    )

    sender.send_text('session-y', 'hello', extra_enter=True)

    assert calls == [
        ['send-keys', '-t', 'session-y', '-l', 'hello'],
        ['send-keys', '-t', 'session-y', 'Enter'],
        ['send-keys', '-t', 'session-y', 'Enter'],
    ]
    assert sleeps == [0.3]


def test_extra_enter_true_deletes_buffer_even_if_second_enter_fails() -> None:
    """If the second Enter raises, the finally block still cleans up the buffer."""
    calls: list[list[str]] = []
    sleeps: list[float] = []

    def _tmux_run(args, **kwargs):
        calls.append(args)
        # Fail ONLY on the standalone second Enter (the one outside the paste compound).
        if args == ['send-keys', '-t', '%1', 'Enter']:
            raise subprocess.CalledProcessError(1, ['tmux', *args])
        return _cp()

    sender = TmuxTextSender(
        tmux_run_fn=_tmux_run,
        looks_like_tmux_target_fn=lambda value: True,
        ensure_not_in_copy_mode_fn=lambda pane_id: None,
        build_buffer_name_fn=lambda **kwargs: 'buf-cleanup',
        sanitize_text_fn=lambda text: text,
        should_use_inline_legacy_send_fn=lambda **kwargs: False,
        env_float_fn=lambda name, default: 0.5,
        sleep_fn=lambda seconds: sleeps.append(seconds),
    )

    with pytest.raises(subprocess.CalledProcessError):
        sender.send_text('%1', 'hello', extra_enter=True)

    # delete-buffer always runs in finally even after second Enter raises.
    assert calls[-1] == ['delete-buffer', '-b', 'buf-cleanup']
    # Sleep DID happen before the failed Enter.
    assert sleeps == [0.5]


def test_extra_enter_default_false_kwarg_omitted_does_not_change_existing_behavior() -> None:
    """Passing send_text without extra_enter kwarg is identical to passing extra_enter=False."""
    calls_a: list[list[str]] = []
    calls_b: list[list[str]] = []
    common_kwargs = dict(
        looks_like_tmux_target_fn=lambda value: True,
        ensure_not_in_copy_mode_fn=lambda pane_id: None,
        build_buffer_name_fn=lambda **kwargs: 'buf-equiv',
        sanitize_text_fn=lambda text: text,
        should_use_inline_legacy_send_fn=lambda **kwargs: False,
        env_float_fn=lambda name, default: default,
        sleep_fn=lambda seconds: (_ for _ in ()).throw(AssertionError('default-equivalence path must not sleep')),
    )
    sender_a = TmuxTextSender(tmux_run_fn=lambda args, **kwargs: calls_a.append(args) or _cp(), **common_kwargs)
    sender_b = TmuxTextSender(tmux_run_fn=lambda args, **kwargs: calls_b.append(args) or _cp(), **common_kwargs)

    sender_a.send_text('%1', 'hello')
    sender_b.send_text('%1', 'hello', extra_enter=False)

    assert calls_a == calls_b
