from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from cli.context import CliContextBuilder
from cli.models import ParsedOpenCommand
from ccbd.socket_client import CcbdClientError
from cli.services.daemon_runtime import CcbdServiceError
import cli.services.open as open_module
from cli.services.open import open_project
from project.resolver import bootstrap_project


def test_open_project_attaches_to_namespace_tmux_session(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-open'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('demo:codex\n', encoding='utf-8')
    bootstrap_project(project_root)
    command = ParsedOpenCommand(project=None)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)

    class _FakeClient:
        def ping(self, target: str) -> dict[str, object]:
            assert target == 'ccbd'
            return {
                'namespace_tmux_socket_path': str(context.paths.ccbd_tmux_socket_path),
                'namespace_tmux_session_name': context.paths.ccbd_tmux_session_name,
                'namespace_workspace_window_name': context.paths.ccbd_tmux_workspace_window_name,
                'namespace_ui_attachable': True,
            }

    calls: list[list[str]] = []

    def _run(args, **kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr('cli.services.open.shutil.which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr(
        'cli.services.open.connect_mounted_daemon',
        lambda context, allow_restart_stale: SimpleNamespace(client=_FakeClient()),
    )
    monkeypatch.setattr('cli.services.open.subprocess.run', _run)

    summary = open_project(context, command)

    assert summary.project_id == context.project.project_id
    assert summary.tmux_socket_path == str(context.paths.ccbd_tmux_socket_path)
    assert summary.tmux_session_name == context.paths.ccbd_tmux_session_name
    assert calls == [
        ['tmux', '-S', str(context.paths.ccbd_tmux_socket_path), 'has-session', '-t', context.paths.ccbd_tmux_session_name],
        [
            'tmux',
            '-S',
            str(context.paths.ccbd_tmux_socket_path),
            'select-window',
            '-t',
            f'{context.paths.ccbd_tmux_session_name}:{context.paths.ccbd_tmux_workspace_window_name}',
        ],
        ['tmux', '-S', str(context.paths.ccbd_tmux_socket_path), 'attach-session', '-t', context.paths.ccbd_tmux_session_name],
    ]


def test_open_project_reports_clean_error_when_session_exits_before_attach(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-open-fail'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('demo:codex\n', encoding='utf-8')
    bootstrap_project(project_root)
    command = ParsedOpenCommand(project=None)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)

    class _FakeClient:
        def ping(self, target: str) -> dict[str, object]:
            assert target == 'ccbd'
            return {
                'namespace_tmux_socket_path': str(context.paths.ccbd_tmux_socket_path),
                'namespace_tmux_session_name': context.paths.ccbd_tmux_session_name,
                'namespace_workspace_window_name': context.paths.ccbd_tmux_workspace_window_name,
                'namespace_ui_attachable': True,
            }

    calls: list[list[str]] = []

    def _run(args, **kwargs):
        del kwargs
        call = list(args)
        calls.append(call)
        if len(calls) == 1:
            return subprocess.CompletedProcess(args=args, returncode=0)
        if len(calls) == 2:
            return subprocess.CompletedProcess(args=args, returncode=0)
        if len(calls) == 3:
            return subprocess.CompletedProcess(args=args, returncode=1)
        if len(calls) == 4:
            return subprocess.CompletedProcess(args=args, returncode=1)
        raise AssertionError(f'unexpected subprocess call: {call}')

    monkeypatch.setattr('cli.services.open.shutil.which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr(
        'cli.services.open.connect_mounted_daemon',
        lambda context, allow_restart_stale: SimpleNamespace(client=_FakeClient()),
    )
    monkeypatch.setattr('cli.services.open.subprocess.run', _run)

    with pytest.raises(RuntimeError, match='session exited before attach completed'):
        open_project(context, command)

    assert calls == [
        ['tmux', '-S', str(context.paths.ccbd_tmux_socket_path), 'has-session', '-t', context.paths.ccbd_tmux_session_name],
        [
            'tmux',
            '-S',
            str(context.paths.ccbd_tmux_socket_path),
            'select-window',
            '-t',
            f'{context.paths.ccbd_tmux_session_name}:{context.paths.ccbd_tmux_workspace_window_name}',
        ],
        ['tmux', '-S', str(context.paths.ccbd_tmux_socket_path), 'attach-session', '-t', context.paths.ccbd_tmux_session_name],
        ['tmux', '-S', str(context.paths.ccbd_tmux_socket_path), 'has-session', '-t', context.paths.ccbd_tmux_session_name],
    ]


def test_open_project_waits_for_config_drift_recovery_before_attach(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-open-config-drift'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('demo:codex\n', encoding='utf-8')
    bootstrap_project(project_root)
    command = ParsedOpenCommand(project=None)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)

    class _FakeClient:
        def ping(self, target: str) -> dict[str, object]:
            assert target == 'ccbd'
            return {
                'namespace_tmux_socket_path': str(context.paths.ccbd_tmux_socket_path),
                'namespace_tmux_session_name': context.paths.ccbd_tmux_session_name,
                'namespace_workspace_window_name': context.paths.ccbd_tmux_workspace_window_name,
                'namespace_ui_attachable': True,
            }

    outcomes = iter(
        (
            CcbdServiceError('mounted ccbd config does not match current .ccb/ccb.config'),
            CcbdServiceError('project ccbd is unmounted; run `ccb [agents...]` first'),
            SimpleNamespace(client=_FakeClient()),
        )
    )

    def _connect(context, allow_restart_stale):
        del context, allow_restart_stale
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    calls: list[list[str]] = []

    def _run(args, **kwargs):
        del kwargs
        calls.append(list(args))
        return subprocess.CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr('cli.services.open.shutil.which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr('cli.services.open.connect_mounted_daemon', _connect)
    monkeypatch.setattr('cli.services.open.subprocess.run', _run)
    monkeypatch.setattr('cli.services.open.time.sleep', lambda seconds: None)

    summary = open_project(context, command)

    assert summary.project_id == context.project.project_id
    assert calls == [
        ['tmux', '-S', str(context.paths.ccbd_tmux_socket_path), 'has-session', '-t', context.paths.ccbd_tmux_session_name],
        [
            'tmux',
            '-S',
            str(context.paths.ccbd_tmux_socket_path),
            'select-window',
            '-t',
            f'{context.paths.ccbd_tmux_session_name}:{context.paths.ccbd_tmux_workspace_window_name}',
        ],
        ['tmux', '-S', str(context.paths.ccbd_tmux_socket_path), 'attach-session', '-t', context.paths.ccbd_tmux_session_name],
    ]


def test_open_project_retries_startup_transient_daemon_connection_errors(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-open-transient-connect'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('demo:codex\n', encoding='utf-8')
    bootstrap_project(project_root)
    command = ParsedOpenCommand(project=None)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)

    class _FakeClient:
        def ping(self, target: str) -> dict[str, object]:
            assert target == 'ccbd'
            return {
                'namespace_tmux_socket_path': str(context.paths.ccbd_tmux_socket_path),
                'namespace_tmux_session_name': context.paths.ccbd_tmux_session_name,
                'namespace_workspace_window_name': context.paths.ccbd_tmux_workspace_window_name,
                'namespace_ui_attachable': True,
            }

    outcomes = iter(
        (
            CcbdServiceError('ccbd is unavailable: socket_unreachable'),
            CcbdServiceError('[Errno 111] Connection refused'),
            CcbdServiceError('[Errno 11] Resource temporarily unavailable'),
            CcbdServiceError('timed out'),
            SimpleNamespace(client=_FakeClient()),
        )
    )

    def _connect(context, allow_restart_stale):
        del context, allow_restart_stale
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    calls: list[list[str]] = []

    def _run(args, **kwargs):
        del kwargs
        calls.append(list(args))
        return subprocess.CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr('cli.services.open.shutil.which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr('cli.services.open.connect_mounted_daemon', _connect)
    monkeypatch.setattr('cli.services.open.subprocess.run', _run)
    monkeypatch.setattr('cli.services.open.time.sleep', lambda seconds: None)

    summary = open_project(context, command)

    assert summary.tmux_session_name == context.paths.ccbd_tmux_session_name
    assert calls[-1] == ['tmux', '-S', str(context.paths.ccbd_tmux_socket_path), 'attach-session', '-t', context.paths.ccbd_tmux_session_name]


def test_open_project_waits_for_namespace_to_become_attachable(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-open-attachable-wait'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('demo:codex\n', encoding='utf-8')
    bootstrap_project(project_root)
    command = ParsedOpenCommand(project=None)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)

    class _FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def ping(self, target: str) -> dict[str, object]:
            assert target == 'ccbd'
            self.calls += 1
            if self.calls == 1:
                return {
                    'namespace_tmux_socket_path': '',
                    'namespace_tmux_session_name': '',
                    'namespace_workspace_window_name': '',
                    'namespace_ui_attachable': False,
                }
            return {
                'namespace_tmux_socket_path': str(context.paths.ccbd_tmux_socket_path),
                'namespace_tmux_session_name': context.paths.ccbd_tmux_session_name,
                'namespace_workspace_window_name': context.paths.ccbd_tmux_workspace_window_name,
                'namespace_ui_attachable': True,
            }

    client = _FakeClient()
    calls: list[list[str]] = []

    def _run(args, **kwargs):
        del kwargs
        calls.append(list(args))
        return subprocess.CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr('cli.services.open.shutil.which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr(
        'cli.services.open.connect_mounted_daemon',
        lambda context, allow_restart_stale: SimpleNamespace(client=client),
    )
    monkeypatch.setattr('cli.services.open.subprocess.run', _run)
    monkeypatch.setattr('cli.services.open.time.sleep', lambda seconds: None)

    summary = open_project(context, command)

    assert summary.tmux_session_name == context.paths.ccbd_tmux_session_name
    assert client.calls == 2
    assert calls[-1] == ['tmux', '-S', str(context.paths.ccbd_tmux_socket_path), 'attach-session', '-t', context.paths.ccbd_tmux_session_name]


def test_open_project_waits_past_legacy_five_second_attach_window(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-open-attachable-slow'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('demo:codex\n', encoding='utf-8')
    bootstrap_project(project_root)
    command = ParsedOpenCommand(project=None)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)

    class _FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def ping(self, target: str) -> dict[str, object]:
            assert target == 'ccbd'
            self.calls += 1
            if self.calls < 7:
                return {
                    'namespace_tmux_socket_path': str(context.paths.ccbd_tmux_socket_path),
                    'namespace_tmux_session_name': context.paths.ccbd_tmux_session_name,
                    'namespace_workspace_window_name': context.paths.ccbd_tmux_workspace_window_name,
                    'namespace_ui_attachable': False,
                }
            return {
                'namespace_tmux_socket_path': str(context.paths.ccbd_tmux_socket_path),
                'namespace_tmux_session_name': context.paths.ccbd_tmux_session_name,
                'namespace_workspace_window_name': context.paths.ccbd_tmux_workspace_window_name,
                'namespace_ui_attachable': True,
            }

    client = _FakeClient()
    now = {'value': -1.0}

    def _time() -> float:
        now['value'] += 1.0
        return now['value']

    def _run(args, **kwargs):
        del kwargs
        return subprocess.CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr('cli.services.open.shutil.which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr(
        'cli.services.open.connect_mounted_daemon',
        lambda context, allow_restart_stale: SimpleNamespace(client=client),
    )
    monkeypatch.setattr('cli.services.open.subprocess.run', _run)
    monkeypatch.setattr('cli.services.open.time.time', _time)
    monkeypatch.setattr('cli.services.open.time.sleep', lambda seconds: None)

    summary = open_project(context, command)

    assert summary.tmux_session_name == context.paths.ccbd_tmux_session_name
    assert client.calls == 7
    assert open_module._OPEN_ATTACH_WAIT_S > 5.0


def test_open_project_retries_transient_ping_errors_while_waiting_for_attachable_namespace(
    tmp_path: Path, monkeypatch
) -> None:
    project_root = tmp_path / 'repo-open-ping-wait'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('demo:codex\n', encoding='utf-8')
    bootstrap_project(project_root)
    command = ParsedOpenCommand(project=None)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)

    class _FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def ping(self, target: str) -> dict[str, object]:
            assert target == 'ccbd'
            self.calls += 1
            if self.calls == 1:
                raise CcbdClientError('[Errno 11] Resource temporarily unavailable') from BlockingIOError(
                    '[Errno 11] Resource temporarily unavailable'
                )
            if self.calls == 2:
                raise CcbdClientError('timed out') from TimeoutError('timed out')
            return {
                'namespace_tmux_socket_path': str(context.paths.ccbd_tmux_socket_path),
                'namespace_tmux_session_name': context.paths.ccbd_tmux_session_name,
                'namespace_workspace_window_name': context.paths.ccbd_tmux_workspace_window_name,
                'namespace_ui_attachable': True,
            }

    client = _FakeClient()
    calls: list[list[str]] = []

    def _run(args, **kwargs):
        del kwargs
        calls.append(list(args))
        return subprocess.CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr('cli.services.open.shutil.which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr(
        'cli.services.open.connect_mounted_daemon',
        lambda context, allow_restart_stale: SimpleNamespace(client=client),
    )
    monkeypatch.setattr('cli.services.open.subprocess.run', _run)
    monkeypatch.setattr('cli.services.open.time.sleep', lambda seconds: None)

    summary = open_project(context, command)

    assert summary.tmux_session_name == context.paths.ccbd_tmux_session_name
    assert client.calls == 3
    assert calls[-1] == ['tmux', '-S', str(context.paths.ccbd_tmux_socket_path), 'attach-session', '-t', context.paths.ccbd_tmux_session_name]


def test_open_project_retries_transport_connection_refused_ping_error(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-open-ping-refused'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('demo:codex\n', encoding='utf-8')
    bootstrap_project(project_root)
    command = ParsedOpenCommand(project=None)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)

    class _FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def ping(self, target: str) -> dict[str, object]:
            assert target == 'ccbd'
            self.calls += 1
            if self.calls == 1:
                raise CcbdClientError('[Errno 111] Connection refused') from ConnectionRefusedError(
                    '[Errno 111] Connection refused'
                )
            return {
                'namespace_tmux_socket_path': str(context.paths.ccbd_tmux_socket_path),
                'namespace_tmux_session_name': context.paths.ccbd_tmux_session_name,
                'namespace_workspace_window_name': context.paths.ccbd_tmux_workspace_window_name,
                'namespace_ui_attachable': True,
            }

    client = _FakeClient()
    calls: list[list[str]] = []

    def _run(args, **kwargs):
        del kwargs
        calls.append(list(args))
        return subprocess.CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr('cli.services.open.shutil.which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr(
        'cli.services.open.connect_mounted_daemon',
        lambda context, allow_restart_stale: SimpleNamespace(client=client),
    )
    monkeypatch.setattr('cli.services.open.subprocess.run', _run)
    monkeypatch.setattr('cli.services.open.time.sleep', lambda seconds: None)

    summary = open_project(context, command)

    assert summary.tmux_session_name == context.paths.ccbd_tmux_session_name
    assert client.calls == 2


def test_open_project_does_not_retry_application_ping_error_with_connection_refused_text(
    tmp_path: Path, monkeypatch
) -> None:
    project_root = tmp_path / 'repo-open-ping-refused-app'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('demo:codex\n', encoding='utf-8')
    bootstrap_project(project_root)
    command = ParsedOpenCommand(project=None)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    calls = 0

    class _FakeClient:
        def ping(self, target: str) -> dict[str, object]:
            nonlocal calls
            assert target == 'ccbd'
            calls += 1
            raise RuntimeError('provider failed: Connection refused')

    monkeypatch.setattr('cli.services.open.shutil.which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr(
        'cli.services.open.connect_mounted_daemon',
        lambda context, allow_restart_stale: SimpleNamespace(client=_FakeClient()),
    )

    with pytest.raises(RuntimeError, match='provider failed: Connection refused'):
        open_project(context, command)

    assert calls == 1


def test_open_project_does_not_retry_application_ping_error_with_timeout_text(
    tmp_path: Path, monkeypatch
) -> None:
    project_root = tmp_path / 'repo-open-ping-timeout-app'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('demo:codex\n', encoding='utf-8')
    bootstrap_project(project_root)
    command = ParsedOpenCommand(project=None)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    calls = 0

    class _FakeClient:
        def ping(self, target: str) -> dict[str, object]:
            nonlocal calls
            assert target == 'ccbd'
            calls += 1
            raise RuntimeError('provider operation timed out')

    monkeypatch.setattr('cli.services.open.shutil.which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr(
        'cli.services.open.connect_mounted_daemon',
        lambda context, allow_restart_stale: SimpleNamespace(client=_FakeClient()),
    )

    with pytest.raises(RuntimeError, match='provider operation timed out'):
        open_project(context, command)

    assert calls == 1


def test_open_project_does_not_retry_service_error_with_application_timeout_text(
    tmp_path: Path, monkeypatch
) -> None:
    project_root = tmp_path / 'repo-open-service-timeout-app'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('demo:codex\n', encoding='utf-8')
    bootstrap_project(project_root)
    command = ParsedOpenCommand(project=None)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    calls = 0

    def _connect(context, allow_restart_stale):
        nonlocal calls
        del context, allow_restart_stale
        calls += 1
        raise CcbdServiceError('provider operation timed out')

    monkeypatch.setattr('cli.services.open.shutil.which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr('cli.services.open.connect_mounted_daemon', _connect)

    with pytest.raises(CcbdServiceError, match='provider operation timed out'):
        open_project(context, command)

    assert calls == 1


def test_open_project_propagates_non_transient_ping_error(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-open-ping-fatal'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('demo:codex\n', encoding='utf-8')
    bootstrap_project(project_root)
    command = ParsedOpenCommand(project=None)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)

    class _FakeClient:
        def ping(self, target: str) -> dict[str, object]:
            assert target == 'ccbd'
            raise RuntimeError('invalid ping payload')

    subprocess_calls: list[list[str]] = []

    def _run(args, **kwargs):
        del kwargs
        subprocess_calls.append(list(args))
        return subprocess.CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr('cli.services.open.shutil.which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr(
        'cli.services.open.connect_mounted_daemon',
        lambda context, allow_restart_stale: SimpleNamespace(client=_FakeClient()),
    )
    monkeypatch.setattr('cli.services.open.subprocess.run', _run)

    with pytest.raises(RuntimeError, match='invalid ping payload'):
        open_project(context, command)

    assert subprocess_calls == []


def test_open_project_reports_last_transient_ping_error_on_attachable_timeout(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-open-ping-timeout'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('demo:codex\n', encoding='utf-8')
    bootstrap_project(project_root)
    command = ParsedOpenCommand(project=None)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)

    class _FakeClient:
        def ping(self, target: str) -> dict[str, object]:
            assert target == 'ccbd'
            raise CcbdClientError('[Errno 11] Resource temporarily unavailable') from BlockingIOError(
                '[Errno 11] Resource temporarily unavailable'
            )

    current_time = 0.0

    def _time() -> float:
        return current_time

    def _sleep(seconds: float) -> None:
        nonlocal current_time
        current_time += seconds

    monkeypatch.setattr('cli.services.open.shutil.which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr(
        'cli.services.open.connect_mounted_daemon',
        lambda context, allow_restart_stale: SimpleNamespace(client=_FakeClient()),
    )
    monkeypatch.setattr('cli.services.open.time.time', _time)
    monkeypatch.setattr('cli.services.open.time.sleep', _sleep)

    with pytest.raises(RuntimeError, match='last transient daemon error: .*Resource temporarily unavailable'):
        open_project(context, command)


def test_open_project_clears_transient_ping_error_after_successful_not_attachable_ping(
    tmp_path: Path, monkeypatch
) -> None:
    project_root = tmp_path / 'repo-open-ping-cleared'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('demo:codex\n', encoding='utf-8')
    bootstrap_project(project_root)
    command = ParsedOpenCommand(project=None)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)

    class _FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def ping(self, target: str) -> dict[str, object]:
            assert target == 'ccbd'
            self.calls += 1
            if self.calls == 1:
                raise CcbdClientError('[Errno 11] Resource temporarily unavailable') from BlockingIOError(
                    '[Errno 11] Resource temporarily unavailable'
                )
            return {
                'namespace_tmux_socket_path': str(context.paths.ccbd_tmux_socket_path),
                'namespace_tmux_session_name': context.paths.ccbd_tmux_session_name,
                'namespace_workspace_window_name': context.paths.ccbd_tmux_workspace_window_name,
                'namespace_ui_attachable': False,
            }

    current_time = 0.0

    def _time() -> float:
        return current_time

    def _sleep(seconds: float) -> None:
        nonlocal current_time
        current_time += seconds

    monkeypatch.setattr('cli.services.open.shutil.which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr(
        'cli.services.open.connect_mounted_daemon',
        lambda context, allow_restart_stale: SimpleNamespace(client=_FakeClient()),
    )
    monkeypatch.setattr('cli.services.open.time.time', _time)
    monkeypatch.setattr('cli.services.open.time.sleep', _sleep)

    with pytest.raises(RuntimeError, match='^project namespace is not attachable; run `ccb` first$'):
        open_project(context, command)


def test_open_project_retries_transient_missing_tmux_session_before_attach(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-open-session-wait'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('demo:codex\n', encoding='utf-8')
    bootstrap_project(project_root)
    command = ParsedOpenCommand(project=None)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)

    class _FakeClient:
        def ping(self, target: str) -> dict[str, object]:
            assert target == 'ccbd'
            return {
                'namespace_tmux_socket_path': str(context.paths.ccbd_tmux_socket_path),
                'namespace_tmux_session_name': context.paths.ccbd_tmux_session_name,
                'namespace_workspace_window_name': context.paths.ccbd_tmux_workspace_window_name,
                'namespace_ui_attachable': True,
            }

    calls: list[list[str]] = []
    has_session_calls = 0

    def _run(args, **kwargs):
        nonlocal has_session_calls
        del kwargs
        call = list(args)
        calls.append(call)
        if call[3] == 'has-session':
            has_session_calls += 1
            return subprocess.CompletedProcess(args=args, returncode=1 if has_session_calls == 1 else 0)
        return subprocess.CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr('cli.services.open.shutil.which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr(
        'cli.services.open.connect_mounted_daemon',
        lambda context, allow_restart_stale: SimpleNamespace(client=_FakeClient()),
    )
    monkeypatch.setattr('cli.services.open.subprocess.run', _run)
    monkeypatch.setattr('cli.services.open.time.sleep', lambda seconds: None)

    summary = open_project(context, command)

    assert summary.tmux_session_name == context.paths.ccbd_tmux_session_name
    assert has_session_calls == 2
    assert calls[-1] == ['tmux', '-S', str(context.paths.ccbd_tmux_socket_path), 'attach-session', '-t', context.paths.ccbd_tmux_session_name]


def test_open_project_retries_transient_missing_workspace_window_before_attach(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-open-window-wait'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('demo:codex\n', encoding='utf-8')
    bootstrap_project(project_root)
    command = ParsedOpenCommand(project=None)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)

    class _FakeClient:
        def ping(self, target: str) -> dict[str, object]:
            assert target == 'ccbd'
            return {
                'namespace_tmux_socket_path': str(context.paths.ccbd_tmux_socket_path),
                'namespace_tmux_session_name': context.paths.ccbd_tmux_session_name,
                'namespace_workspace_window_name': context.paths.ccbd_tmux_workspace_window_name,
                'namespace_ui_attachable': True,
            }

    calls: list[list[str]] = []
    select_calls = 0

    def _run(args, **kwargs):
        nonlocal select_calls
        del kwargs
        call = list(args)
        calls.append(call)
        if call[3] == 'select-window':
            select_calls += 1
            return subprocess.CompletedProcess(args=args, returncode=1 if select_calls == 1 else 0)
        return subprocess.CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr('cli.services.open.shutil.which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr(
        'cli.services.open.connect_mounted_daemon',
        lambda context, allow_restart_stale: SimpleNamespace(client=_FakeClient()),
    )
    monkeypatch.setattr('cli.services.open.subprocess.run', _run)
    monkeypatch.setattr('cli.services.open.time.sleep', lambda seconds: None)

    summary = open_project(context, command)

    assert summary.tmux_session_name == context.paths.ccbd_tmux_session_name
    assert select_calls == 2
    assert calls[-1] == ['tmux', '-S', str(context.paths.ccbd_tmux_socket_path), 'attach-session', '-t', context.paths.ccbd_tmux_session_name]


def test_open_project_retries_transient_attach_failure_when_session_survives(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-open-attach-retry'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('demo:codex\n', encoding='utf-8')
    bootstrap_project(project_root)
    command = ParsedOpenCommand(project=None)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)

    class _FakeClient:
        def ping(self, target: str) -> dict[str, object]:
            assert target == 'ccbd'
            return {
                'namespace_tmux_socket_path': str(context.paths.ccbd_tmux_socket_path),
                'namespace_tmux_session_name': context.paths.ccbd_tmux_session_name,
                'namespace_workspace_window_name': context.paths.ccbd_tmux_workspace_window_name,
                'namespace_ui_attachable': True,
            }

    calls: list[list[str]] = []
    attach_calls = 0

    def _run(args, **kwargs):
        nonlocal attach_calls
        del kwargs
        call = list(args)
        calls.append(call)
        if call[3] == 'attach-session':
            attach_calls += 1
            return subprocess.CompletedProcess(args=args, returncode=1 if attach_calls == 1 else 0)
        return subprocess.CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr('cli.services.open.shutil.which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr(
        'cli.services.open.connect_mounted_daemon',
        lambda context, allow_restart_stale: SimpleNamespace(client=_FakeClient()),
    )
    monkeypatch.setattr('cli.services.open.subprocess.run', _run)
    monkeypatch.setattr('cli.services.open.time.sleep', lambda seconds: None)

    summary = open_project(context, command)

    assert summary.tmux_session_name == context.paths.ccbd_tmux_session_name
    assert attach_calls == 2


def test_open_project_attaches_with_inherited_stdio(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-open-attach-stdio'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('demo:codex\n', encoding='utf-8')
    bootstrap_project(project_root)
    command = ParsedOpenCommand(project=None)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)

    class _FakeClient:
        def ping(self, target: str) -> dict[str, object]:
            assert target == 'ccbd'
            return {
                'namespace_tmux_socket_path': str(context.paths.ccbd_tmux_socket_path),
                'namespace_tmux_session_name': context.paths.ccbd_tmux_session_name,
                'namespace_workspace_window_name': context.paths.ccbd_tmux_workspace_window_name,
                'namespace_ui_attachable': True,
            }

    attach_kwargs: list[dict[str, object]] = []

    def _run(args, **kwargs):
        call = list(args)
        if call[3] == 'attach-session':
            attach_kwargs.append(dict(kwargs))
        return subprocess.CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr('cli.services.open.shutil.which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr(
        'cli.services.open.connect_mounted_daemon',
        lambda context, allow_restart_stale: SimpleNamespace(client=_FakeClient()),
    )
    monkeypatch.setattr('cli.services.open.subprocess.run', _run)
    monkeypatch.setattr('cli.services.open.time.sleep', lambda seconds: None)

    summary = open_project(context, command)

    assert summary.tmux_session_name == context.paths.ccbd_tmux_session_name
    assert len(attach_kwargs) == 1
    assert attach_kwargs[0]['check'] is False
    assert 'env' in attach_kwargs[0]
    assert 'capture_output' not in attach_kwargs[0]
    assert 'stdout' not in attach_kwargs[0]
    assert 'stderr' not in attach_kwargs[0]
    assert 'text' not in attach_kwargs[0]
