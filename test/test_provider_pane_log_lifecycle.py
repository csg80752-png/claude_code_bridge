from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from provider_backends.claude.session_runtime.lifecycle_runtime import ensure_pane as ensure_claude_pane
from provider_backends.codex.session_runtime.lifecycle import ensure_pane as ensure_codex_pane
from provider_backends.droid.session_runtime.lifecycle import ensure_pane as ensure_droid_pane
from provider_backends.gemini.session_runtime.lifecycle import ensure_pane as ensure_gemini_pane
from provider_backends.opencode.session_runtime.lifecycle import ensure_pane as ensure_opencode_pane
from provider_backends.pane_log_support.session import PaneLogProjectSessionBase


class _FakeBackend:
    def __init__(self) -> None:
        self.commands = {'%4': 'bash'}
        self.respawned: list[dict[str, object]] = []
        self.options: list[tuple[str, str, str]] = []

    def is_alive(self, pane_id: str) -> bool:
        return pane_id == '%4'

    def pane_exists(self, pane_id: str) -> bool:
        return pane_id == '%4'

    def pane_current_command(self, pane_id: str) -> str | None:
        return self.commands.get(pane_id)

    def respawn_pane(self, pane_id: str, *, cmd: str, cwd: str | None = None, remain_on_exit: bool = True) -> None:
        self.respawned.append(
            {
                'pane_id': pane_id,
                'cmd': cmd,
                'cwd': cwd,
                'remain_on_exit': remain_on_exit,
            }
        )
        self.commands[pane_id] = 'node'

    def set_pane_title(self, pane_id: str, title: str) -> None:
        return None

    def set_pane_user_option(self, pane_id: str, key: str, value: str) -> None:
        self.options.append((pane_id, key, value))

    def ensure_pane_log(self, pane_id: str) -> None:
        return None


class _Session(PaneLogProjectSessionBase):
    def __init__(self, *, session_file: Path, backend: _FakeBackend) -> None:
        super().__init__(
            session_file=session_file,
            data={
                'terminal': 'tmux',
                'pane_id': '%4',
                'work_dir': str(session_file.parent),
                'runtime_dir': str(session_file.parent),
                'start_cmd': 'provider --resume',
            },
        )
        self._backend = backend

    def backend(self):
        return self._backend


@pytest.mark.parametrize(
    'ensure_pane_fn',
    [
        ensure_claude_pane,
        ensure_codex_pane,
        ensure_droid_pane,
        ensure_gemini_pane,
        ensure_opencode_pane,
    ],
)
def test_provider_lifecycle_respawns_owned_shell_pane(
    ensure_pane_fn: Callable[[object], tuple[bool, str]],
    tmp_path: Path,
) -> None:
    backend = _FakeBackend()
    session = _Session(session_file=tmp_path / 'session.json', backend=backend)

    ok, pane_or_err = ensure_pane_fn(session)

    assert ok is True
    assert pane_or_err == '%4'
    assert backend.respawned == [
        {
            'pane_id': '%4',
            'cmd': 'provider --resume',
            'cwd': str(tmp_path),
            'remain_on_exit': True,
        }
    ]
