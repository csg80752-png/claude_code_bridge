from __future__ import annotations

import subprocess

import pytest

from terminal_runtime.tmux_send import TmuxTextSender


def _cp(*, stdout: str = '', returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=['tmux'], returncode=returncode, stdout=stdout, stderr='')


def test_tmux_text_sender_deletes_buffer_after_paste_failure() -> None:
    calls: list[list[str]] = []
    sender = TmuxTextSender(
        tmux_run_fn=lambda args, **kwargs: calls.append(args) or (
            (_ for _ in ()).throw(subprocess.CalledProcessError(1, ['tmux', *args]))
            if args and args[0] == 'paste-buffer'
            else _cp()
        ),
        looks_like_tmux_target_fn=lambda value: True,
        ensure_not_in_copy_mode_fn=lambda pane_id: calls.append(['ensure-copy-mode', pane_id]),
        build_buffer_name_fn=lambda **kwargs: 'buf-1',
        sanitize_text_fn=lambda text: text,
        should_use_inline_legacy_send_fn=lambda **kwargs: False,
        env_float_fn=lambda name, default: 0.0,
    )

    with pytest.raises(subprocess.CalledProcessError):
        sender.send_text('%1', 'hello')

    assert calls[0] == ['ensure-copy-mode', '%1']
    assert ['load-buffer', '-b', 'buf-1', '-'] in calls
    assert ['paste-buffer', '-p', '-t', '%1', '-b', 'buf-1', ';', 'send-keys', '-t', '%1', 'Enter'] in calls
    assert calls[-1] == ['delete-buffer', '-b', 'buf-1']


def test_tmux_text_sender_uses_inline_legacy_mode_for_session_targets() -> None:
    calls: list[list[str]] = []
    sender = TmuxTextSender(
        tmux_run_fn=lambda args, **kwargs: calls.append(args) or _cp(),
        looks_like_tmux_target_fn=lambda value: False,
        ensure_not_in_copy_mode_fn=lambda pane_id: None,
        build_buffer_name_fn=lambda **kwargs: 'buf-2',
        sanitize_text_fn=lambda text: text,
        should_use_inline_legacy_send_fn=lambda **kwargs: True,
        env_float_fn=lambda name, default: 0.0,
    )

    sender.send_text('session-x', 'hello')

    assert calls == [
        ['send-keys', '-t', 'session-x', '-l', 'hello'],
        ['send-keys', '-t', 'session-x', 'Enter'],
    ]


def test_tmux_text_sender_default_path_sends_single_enter() -> None:
    """Default extra_enter=False: paste compound contains exactly one Enter, no trailing send-keys."""
    calls: list[list[str]] = []
    sender = TmuxTextSender(
        tmux_run_fn=lambda args, **kwargs: calls.append(args) or _cp(),
        looks_like_tmux_target_fn=lambda value: True,
        ensure_not_in_copy_mode_fn=lambda pane_id: None,
        build_buffer_name_fn=lambda **kwargs: 'buf-default',
        sanitize_text_fn=lambda text: text,
        should_use_inline_legacy_send_fn=lambda **kwargs: False,
        env_float_fn=lambda name, default: default,
        sleep_fn=lambda seconds: (_ for _ in ()).throw(AssertionError('default path must not sleep')),
    )

    sender.send_text('%1', 'hello')

    paste_calls = [c for c in calls if c and c[0] == 'paste-buffer']
    enter_only_calls = [c for c in calls if c == ['send-keys', '-t', '%1', 'Enter']]
    assert len(paste_calls) == 1
    assert paste_calls[0] == [
        'paste-buffer', '-p', '-t', '%1', '-b', 'buf-default',
        ';', 'send-keys', '-t', '%1', 'Enter',
    ]
    # No standalone Enter send-keys when extra_enter is False (Enter is inside the paste compound).
    assert enter_only_calls == []
    # delete-buffer always runs in finally.
    assert calls[-1] == ['delete-buffer', '-b', 'buf-default']


def test_race_pane_dies_mid_send_uses_single_tmux_command_for_paste_and_enter() -> None:
    calls: list[list[str]] = []

    def _tmux_run(args, **kwargs):
        calls.append(args)
        if args and args[0] == 'send-keys' and args[-1] == 'Enter':
            raise AssertionError('Enter must not be sent in a separate command after paste')
        return _cp()

    sender = TmuxTextSender(
        tmux_run_fn=_tmux_run,
        looks_like_tmux_target_fn=lambda value: True,
        ensure_not_in_copy_mode_fn=lambda pane_id: calls.append(['ensure-copy-mode', pane_id]),
        build_buffer_name_fn=lambda **kwargs: 'buf-race',
        sanitize_text_fn=lambda text: text,
        should_use_inline_legacy_send_fn=lambda **kwargs: False,
        env_float_fn=lambda name, default: 99.0,
        sleep_fn=lambda seconds: (_ for _ in ()).throw(AssertionError('paste/enter path must not sleep')),
    )

    sender.send_text('%1', '[CCB] job=job_1234abcd from=agent1 bytes=1 pend=ccb-pend')

    paste_calls = [call for call in calls if call and call[0] == 'paste-buffer']
    assert len(paste_calls) == 1
    assert paste_calls[0] == [
        'paste-buffer', '-p', '-t', '%1', '-b', 'buf-race',
        ';', 'send-keys', '-t', '%1', 'Enter',
    ]
