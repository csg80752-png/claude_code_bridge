from __future__ import annotations

from ccbd.services.dispatcher_runtime.reply_delivery_runtime import preparation_service
from ccbd.services.dispatcher_runtime.reply_delivery_runtime.cmd_readiness_probes import ReadinessOutcome


class _SignalBackend:
    def __init__(self, *, signal: str, content: str) -> None:
        self.signal = signal
        self.content = content

    def get_ccb_ready_signal(self, pane_id: str, command: str) -> str:
        assert pane_id == "%1"
        assert command == "claude"
        return self.signal

    def get_pane_content(self, pane_id: str, *, lines: int) -> str:
        assert pane_id == "%1"
        assert lines > 0
        return self.content


def test_cmd_readiness_signal_default_off_uses_heuristic_even_when_signal_busy(monkeypatch) -> None:
    monkeypatch.delenv("CCB_CMD_READINESS_SIGNAL", raising=False)
    backend = _SignalBackend(signal="busy", content="❯ ")

    assert preparation_service._cmd_pane_readiness(backend, "%1", "claude") is ReadinessOutcome.READY


def test_cmd_readiness_signal_opt_in_accepts_ready_without_screen_probe(monkeypatch) -> None:
    monkeypatch.setenv("CCB_CMD_READINESS_SIGNAL", "1")
    backend = _SignalBackend(signal="ready", content="Do you want to allow this command?\n❯ ")

    assert preparation_service._cmd_pane_readiness(backend, "%1", "claude") is ReadinessOutcome.READY


def test_cmd_readiness_signal_opt_in_rejects_busy_before_heuristic(monkeypatch) -> None:
    monkeypatch.setenv("CCB_CMD_READINESS_SIGNAL", "1")
    backend = _SignalBackend(signal="busy", content="❯ ")

    assert preparation_service._cmd_pane_readiness(backend, "%1", "claude") is ReadinessOutcome.NOT_READY


def test_cmd_readiness_signal_unknown_falls_back_to_heuristic(monkeypatch) -> None:
    monkeypatch.setenv("CCB_CMD_READINESS_SIGNAL", "1")
    backend = _SignalBackend(signal="", content="❯ ")

    assert preparation_service._cmd_pane_readiness(backend, "%1", "claude") is ReadinessOutcome.READY
