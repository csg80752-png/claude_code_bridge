from __future__ import annotations

from types import SimpleNamespace

import pytest

from ccbd.services.dispatcher_runtime.reply_delivery_runtime import preparation_service
from ccbd.services.dispatcher_runtime.reply_delivery_runtime.cmd_transport_planner import resolve_cmd_delivery_mode
from mailbox_kernel import InboundEventStatus, InboundEventType


class _Backend:
    def __init__(self, *, alive: bool = True, pane_text: str = "❯ ") -> None:
        self._alive = alive
        self._pane_text = pane_text
        self.injected: list[str] = []

    def is_alive(self, pane_id: str) -> bool:
        return self._alive

    def get_pane_content(self, pane_id: str, lines: int = 120) -> str:
        return self._pane_text

    def send_text_to_pane(self, pane_id: str, text: str, **kwargs) -> None:
        self.injected.append(text)


def _dispatcher(tmp_path, *, compatible: bool = True):
    head = SimpleNamespace(
        inbound_event_id="evt_1",
        event_type=InboundEventType.TASK_REPLY,
        status=InboundEventStatus.QUEUED,
        payload_ref="reply:rep_1",
    )
    reply = SimpleNamespace(
        attempt_id="att_1",
        agent_name="agent1",
        reply_id="rep_1",
        terminal_status=SimpleNamespace(value="succeeded"),
        diagnostics={},
        reply="body",
    )
    return SimpleNamespace(
        _message_bureau_control=SimpleNamespace(
            _mailbox_kernel=SimpleNamespace(
                head_pending_event=lambda agent_name: head,
                pending_events=lambda agent_name, *, event_type=None: (
                    (head,) if (event_type is None or head.event_type is event_type) else ()
                ),
            ),
            _reply_store=SimpleNamespace(get_latest=lambda reply_id: reply),
            _attempt_store=SimpleNamespace(get_latest=lambda attempt_id: SimpleNamespace(job_id="job_1234abcd")),
        ),
        _layout=SimpleNamespace(project_root=tmp_path),
        _clock=lambda: "2026-05-03T00:00:00Z",
        _cmd_delivery_mode_result=resolve_cmd_delivery_mode(
            project_root=tmp_path,
            header_only_compatible=compatible,
        ),
        get_job=lambda job_id: SimpleNamespace(job_id="job_1234abcd", request=SimpleNamespace(task_id="task_1")),
    )


@pytest.mark.parametrize(
    ("foreground", "pane_text", "alive", "compatible", "expected_inject"),
    [
        ("bash", "❯ ", True, True, False),
        ("node", "❯ ", True, True, False),
        ("claude", "❯ ", True, False, True),
        ("claude", "Do you want to allow this?\ncontext\nwrap\nlines\n❯ ", True, True, False),
        ("claude", "❯ ", False, True, False),
        ("claude", "❯ ", True, True, True),
    ],
)
def test_header_delivery_failure_mode_matrix(
    monkeypatch,
    tmp_path,
    foreground: str,
    pane_text: str,
    alive: bool,
    compatible: bool,
    expected_inject: bool,
) -> None:
    monkeypatch.setenv("CCB_CMD_DELIVERY_MODE", "header_only")
    backend = _Backend(alive=alive, pane_text=pane_text)
    monkeypatch.setattr(preparation_service, "_discover_cmd_pane_id", lambda dispatcher: "%1")
    monkeypatch.setattr(preparation_service, "_get_tmux_backend", lambda dispatcher: backend)
    monkeypatch.setattr(preparation_service, "_cmd_pane_foreground_command", lambda backend, pane_id: foreground)

    preparation_service._deliver_cmd_replies(_dispatcher(tmp_path, compatible=compatible))

    assert bool(backend.injected) is expected_inject
