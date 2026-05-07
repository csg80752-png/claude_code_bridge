from __future__ import annotations

from types import SimpleNamespace

from cli.context import CliContextBuilder
from cli.models import ParsedPsCommand
from cli.services.ps import ps_summary
from mailbox_kernel.models import MailboxRecord, MailboxState
from mailbox_kernel.store import MailboxStore
from project.resolver import bootstrap_project


def test_ps_summary_includes_tmux_socket_and_pane_observation(tmp_path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-ps'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('agent1:codex\n', encoding='utf-8')
    bootstrap_project(project_root)
    context = CliContextBuilder().build(ParsedPsCommand(project=None), cwd=project_root, bootstrap_if_missing=False)

    runtime = SimpleNamespace(
        state=SimpleNamespace(value='idle'),
        queue_depth=0,
        backend_type='pane_backed',
        binding_source=SimpleNamespace(value='provider-session'),
        runtime_ref='tmux:%52',
        session_ref='session-2',
        session_file=None,
        session_id='session-2',
        workspace_path=str(project_root / '.ccb' / 'workspaces' / 'agent1'),
        terminal_backend='tmux',
        tmux_socket_name='sock-a',
        tmux_socket_path='/tmp/ccb.sock',
        pane_id='%41',
        active_pane_id='%52',
        pane_title_marker='CCB-agent1-demo',
        pane_state='alive',
    )

    class _Store:
        def __init__(self, paths) -> None:
            self.paths = paths

        def load(self, agent_name: str):
            return runtime if agent_name == 'agent1' else None

    monkeypatch.setattr('cli.services.ps.AgentRuntimeStore', _Store)
    monkeypatch.setattr('cli.services.ps.ping_local_state', lambda context: SimpleNamespace(mount_state='mounted'))

    payload = ps_summary(context, ParsedPsCommand(project=None))

    assert payload['ccbd_state'] == 'mounted'
    assert len(payload['agents']) == 1
    agent = payload['agents'][0]
    assert agent['runtime_ref'] == 'tmux:%52'
    assert agent['session_ref'] == 'session-2'
    assert agent['tmux_socket_name'] == 'sock-a'
    assert agent['tmux_socket_path'] == '/tmp/ccb.sock'
    assert agent['pane_id'] == '%41'
    assert agent['active_pane_id'] == '%52'
    assert agent['pane_title_marker'] == 'CCB-agent1-demo'
    assert agent['pane_state'] == 'alive'


def test_ps_summary_prefers_mailbox_queue_depth_over_stale_runtime_record(tmp_path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-ps-queue'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('agent1:codex\n', encoding='utf-8')
    bootstrap_project(project_root)
    context = CliContextBuilder().build(ParsedPsCommand(project=None), cwd=project_root, bootstrap_if_missing=False)

    runtime = SimpleNamespace(
        state=SimpleNamespace(value='idle'),
        queue_depth=6,
        backend_type='pane_backed',
        binding_source=SimpleNamespace(value='provider-session'),
        runtime_ref='tmux:%52',
        session_ref='session-2',
        session_file=None,
        session_id='session-2',
        workspace_path=str(project_root / '.ccb' / 'workspaces' / 'agent1'),
        terminal_backend='tmux',
        tmux_socket_name='sock-a',
        tmux_socket_path='/tmp/ccb.sock',
        pane_id='%41',
        active_pane_id='%52',
        pane_title_marker='CCB-agent1-demo',
        pane_state='alive',
    )
    mailbox = SimpleNamespace(queue_depth=0)

    class _RuntimeStore:
        def __init__(self, paths) -> None:
            self.paths = paths

        def load(self, agent_name: str):
            return runtime if agent_name == 'agent1' else None

    class _MailboxStore:
        def __init__(self, paths) -> None:
            self.paths = paths

        def load(self, agent_name: str):
            return mailbox if agent_name == 'agent1' else None

    monkeypatch.setattr('cli.services.ps.AgentRuntimeStore', _RuntimeStore)
    monkeypatch.setattr('cli.services.ps.MailboxStore', _MailboxStore, raising=False)
    monkeypatch.setattr('cli.services.ps.ping_local_state', lambda context: SimpleNamespace(mount_state='mounted'))

    payload = ps_summary(context, ParsedPsCommand(project=None))

    assert payload['agents'][0]['queue_depth'] == 0


def test_ps_summary_reads_durable_mailbox_queue_depth_over_runtime_record(tmp_path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-ps-queue-durable'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('agent1:codex\n', encoding='utf-8')
    bootstrap_project(project_root)
    context = CliContextBuilder().build(ParsedPsCommand(project=None), cwd=project_root, bootstrap_if_missing=False)

    MailboxStore(context.paths).save(
        MailboxRecord(
            mailbox_id='mbx_agent1',
            agent_name='agent1',
            active_inbound_event_id=None,
            queue_depth=0,
            pending_reply_count=0,
            last_inbound_started_at=None,
            last_inbound_finished_at=None,
            mailbox_state=MailboxState.IDLE,
            lease_version=1,
            updated_at='2026-05-07T00:00:00Z',
        )
    )

    runtime = SimpleNamespace(
        state=SimpleNamespace(value='idle'),
        queue_depth=6,
        backend_type='pane_backed',
        binding_source=SimpleNamespace(value='provider-session'),
        runtime_ref='tmux:%52',
        session_ref='session-2',
        session_file=None,
        session_id='session-2',
        workspace_path=str(project_root / '.ccb' / 'workspaces' / 'agent1'),
        terminal_backend='tmux',
        tmux_socket_name='sock-a',
        tmux_socket_path='/tmp/ccb.sock',
        pane_id='%41',
        active_pane_id='%52',
        pane_title_marker='CCB-agent1-demo',
        pane_state='alive',
    )

    class _RuntimeStore:
        def __init__(self, paths) -> None:
            self.paths = paths

        def load(self, agent_name: str):
            return runtime if agent_name == 'agent1' else None

    monkeypatch.setattr('cli.services.ps.AgentRuntimeStore', _RuntimeStore)
    monkeypatch.setattr('cli.services.ps.ping_local_state', lambda context: SimpleNamespace(mount_state='mounted'))

    payload = ps_summary(context, ParsedPsCommand(project=None))

    assert payload['agents'][0]['queue_depth'] == 0


def test_ps_summary_preserves_nonzero_durable_mailbox_queue_depth(tmp_path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-ps-queue-nonzero'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('agent1:codex\n', encoding='utf-8')
    bootstrap_project(project_root)
    context = CliContextBuilder().build(ParsedPsCommand(project=None), cwd=project_root, bootstrap_if_missing=False)

    MailboxStore(context.paths).save(
        MailboxRecord(
            mailbox_id='mbx_agent1',
            agent_name='agent1',
            active_inbound_event_id=None,
            queue_depth=2,
            pending_reply_count=0,
            last_inbound_started_at=None,
            last_inbound_finished_at=None,
            mailbox_state=MailboxState.IDLE,
            lease_version=1,
            updated_at='2026-05-07T00:00:00Z',
        )
    )

    runtime = SimpleNamespace(
        state=SimpleNamespace(value='idle'),
        queue_depth=0,
        backend_type='pane_backed',
        binding_source=SimpleNamespace(value='provider-session'),
        runtime_ref='tmux:%52',
        session_ref='session-2',
        session_file=None,
        session_id='session-2',
        workspace_path=str(project_root / '.ccb' / 'workspaces' / 'agent1'),
        terminal_backend='tmux',
        tmux_socket_name='sock-a',
        tmux_socket_path='/tmp/ccb.sock',
        pane_id='%41',
        active_pane_id='%52',
        pane_title_marker='CCB-agent1-demo',
        pane_state='alive',
    )

    class _RuntimeStore:
        def __init__(self, paths) -> None:
            self.paths = paths

        def load(self, agent_name: str):
            return runtime if agent_name == 'agent1' else None

    monkeypatch.setattr('cli.services.ps.AgentRuntimeStore', _RuntimeStore)
    monkeypatch.setattr('cli.services.ps.ping_local_state', lambda context: SimpleNamespace(mount_state='mounted'))

    payload = ps_summary(context, ParsedPsCommand(project=None))

    assert payload['agents'][0]['queue_depth'] == 2
