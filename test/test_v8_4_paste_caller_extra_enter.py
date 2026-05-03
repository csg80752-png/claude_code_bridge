"""Caller integration test for the v8.4 paste-collapse fix.

Verifies the per-provider toggle of extra_enter:
  - claude communicator + claude execution start → extra_enter=True
  - codex communicator + codex execution start → extra_enter=False (default)

Test design: a recording backend implements both send_text and
send_text_to_pane, capturing the kwarg actually passed. This pins the
provider-routing decision at the seam between the provider runtime
(which knows it's claude) and the terminal backend (which is
provider-agnostic).
"""

from __future__ import annotations

from types import SimpleNamespace


class RecordingBackend:
    """Records every send_text / send_text_to_pane call with kwargs."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def send_text(self, pane_id: str, text: str, **kwargs) -> None:
        self.calls.append({'method': 'send_text', 'pane_id': pane_id, 'text': text, **kwargs})

    def send_text_to_pane(self, pane_id: str, text: str, **kwargs) -> None:
        self.calls.append({'method': 'send_text_to_pane', 'pane_id': pane_id, 'text': text, **kwargs})


def test_claude_communicator_send_via_terminal_passes_extra_enter_true() -> None:
    """ClaudeCommunicator._send_via_terminal calls backend.send_text with extra_enter=True."""
    from provider_backends.claude.comm_runtime.communicator_facade import ClaudeCommunicator

    backend = RecordingBackend()
    comm = ClaudeCommunicator.__new__(ClaudeCommunicator)
    comm.backend = backend
    comm.pane_id = '%1'

    ok = comm._send_via_terminal('hello from claude')

    assert ok is True
    assert backend.calls == [{
        'method': 'send_text',
        'pane_id': '%1',
        'text': 'hello from claude',
        'extra_enter': True,
    }]


def test_codex_communicator_send_via_terminal_does_not_pass_extra_enter() -> None:
    """CodexCommunicator._send_via_terminal calls backend.send_text WITHOUT extra_enter (default False)."""
    from provider_backends.codex.comm_runtime.communicator_facade import CodexCommunicator

    backend = RecordingBackend()
    comm = CodexCommunicator.__new__(CodexCommunicator)
    comm.backend = backend
    comm.pane_id = '%2'

    comm._send_via_terminal('hello from codex')

    assert len(backend.calls) == 1
    call = backend.calls[0]
    assert call['method'] == 'send_text'
    assert call['pane_id'] == '%2'
    assert call['text'] == 'hello from codex'
    # codex must NOT pass extra_enter (would erroneously double-Enter the codex CLI).
    assert 'extra_enter' not in call


def test_claude_send_prompt_passes_extra_enter_true_via_runtime_helper() -> None:
    """claude.execution_runtime.start.send_prompt routes extra_enter=True through send_prompt_to_runtime_target."""
    from provider_backends.claude.execution_runtime.start import send_prompt

    backend = RecordingBackend()
    send_prompt(backend, '%3', 'startup prompt')

    # Backend exposes both methods; send_prompt_to_runtime_target prefers send_text_to_pane.
    assert backend.calls == [{
        'method': 'send_text_to_pane',
        'pane_id': '%3',
        'text': 'startup prompt',
        'extra_enter': True,
    }]


def test_codex_send_prompt_does_not_pass_extra_enter() -> None:
    """codex.execution_runtime.start path uses send_prompt_to_runtime_target without extra_enter."""
    from provider_execution.common import send_prompt_to_runtime_target

    backend = RecordingBackend()
    send_prompt_to_runtime_target(backend, '%4', 'codex startup')

    # Default extra_enter=False: kwarg must NOT be forwarded.
    assert len(backend.calls) == 1
    call = backend.calls[0]
    assert call['method'] == 'send_text_to_pane'
    assert 'extra_enter' not in call


def test_send_prompt_to_runtime_target_falls_back_to_send_text_when_no_strict_send() -> None:
    """When backend has no send_text_to_pane, fallback to send_text — without extra_enter on default."""

    class FallbackBackend:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def send_text(self, pane_id: str, text: str, **kwargs) -> None:
            self.calls.append({'pane_id': pane_id, 'text': text, **kwargs})

    from provider_execution.common import send_prompt_to_runtime_target

    backend = FallbackBackend()
    send_prompt_to_runtime_target(backend, '%5', 'fallback text')

    assert len(backend.calls) == 1
    assert 'extra_enter' not in backend.calls[0]


def test_send_prompt_to_runtime_target_forwards_extra_enter_to_send_text_fallback() -> None:
    """When backend lacks send_text_to_pane and extra_enter=True, kwarg flows to send_text."""

    class FallbackBackend:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def send_text(self, pane_id: str, text: str, **kwargs) -> None:
            self.calls.append({'pane_id': pane_id, 'text': text, **kwargs})

    from provider_execution.common import send_prompt_to_runtime_target

    backend = FallbackBackend()
    send_prompt_to_runtime_target(backend, '%6', 'claude fallback', extra_enter=True)

    assert backend.calls == [{
        'pane_id': '%6',
        'text': 'claude fallback',
        'extra_enter': True,
    }]
