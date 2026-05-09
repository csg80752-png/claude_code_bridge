from __future__ import annotations

from pathlib import Path

from agents.config_loader import load_project_config
from cli.context import CliContext
from cli.models import ParsedStartCommand
import cli.services.tmux_start_layout as tmux_start_layout
from project.resolver import bootstrap_project
from storage.paths import PathLayout
from terminal_runtime.tmux_identity import pane_visual


def _context(project_root: Path) -> CliContext:
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    project = bootstrap_project(project_root)
    command = ParsedStartCommand(project=None, agent_names=(), restore=False, auto_permission=False)
    return CliContext(command=command, cwd=project_root, project=project, paths=PathLayout(project_root))


def test_prepare_tmux_start_layout_uses_current_pane_as_cmd_anchor(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-layout'
    ctx = _context(project_root)
    config = load_project_config(project_root).config
    calls: list[tuple[str, str, str]] = []

    class FakeTmuxBackend:
        def get_current_pane_id(self) -> str:
            return '%0'

        def set_pane_title(self, pane_id: str, title: str) -> None:
            calls.append(('title', pane_id, title))

        def set_pane_user_option(self, pane_id: str, name: str, value: str) -> None:
            calls.append((name, pane_id, value))

        def create_pane(self, cmd: str, cwd: str, direction: str = 'right', percent: int = 50, parent_pane: str | None = None) -> str:
            mapping = {
                ('right', '%0'): '%1',
                ('bottom', '%0'): '%2',
                ('right', '%2'): '%3',
            }
            return mapping[(direction, str(parent_pane))]

    monkeypatch.setattr(tmux_start_layout, 'TmuxBackend', FakeTmuxBackend)

    layout = tmux_start_layout.prepare_tmux_start_layout(
        ctx,
        config=config,
        targets=('agent1', 'agent2', 'agent3'),
    )

    assert layout.cmd_pane_id == '%0'
    assert layout.agent_panes == {'agent1': '%1', 'agent2': '%2', 'agent3': '%3'}
    assert ('title', '%0', 'cmd') in calls
    assert ('title', '%1', 'agent1') in calls
    assert ('title', '%2', 'agent2') in calls
    assert ('title', '%3', 'agent3') in calls


def test_prepare_tmux_start_layout_assigns_slot_stable_styles(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-layout-styles'
    ctx = _context(project_root)
    config = load_project_config(project_root).config
    options: dict[tuple[str, str], str] = {}
    styles: dict[str, tuple[str | None, str | None]] = {}

    class FakeTmuxBackend:
        def get_current_pane_id(self) -> str:
            return '%0'

        def set_pane_title(self, pane_id: str, title: str) -> None:
            return None

        def set_pane_user_option(self, pane_id: str, name: str, value: str) -> None:
            options[(pane_id, name)] = value

        def set_pane_style(
            self,
            pane_id: str,
            *,
            border_style: str | None = None,
            active_border_style: str | None = None,
        ) -> None:
            styles[pane_id] = (border_style, active_border_style)

        def create_pane(self, cmd: str, cwd: str, direction: str = 'right', percent: int = 50, parent_pane: str | None = None) -> str:
            mapping = {
                ('right', '%0'): '%1',
                ('bottom', '%0'): '%2',
                ('right', '%2'): '%3',
            }
            return mapping[(direction, str(parent_pane))]

    monkeypatch.setattr(tmux_start_layout, 'TmuxBackend', FakeTmuxBackend)

    layout = tmux_start_layout.prepare_tmux_start_layout(
        ctx,
        config=config,
        targets=('agent1', 'agent2', 'agent3'),
    )

    assert layout.agent_panes == {'agent1': '%1', 'agent2': '%2', 'agent3': '%3'}
    cmd_visual = pane_visual(project_id=ctx.project.project_id, slot_key='cmd', is_cmd=True)
    agent1_visual = pane_visual(project_id=ctx.project.project_id, slot_key='agent1', order_index=0)
    agent2_visual = pane_visual(project_id=ctx.project.project_id, slot_key='agent2', order_index=1)
    agent3_visual = pane_visual(project_id=ctx.project.project_id, slot_key='agent3', order_index=2)
    assert options[('%0', '@ccb_label_style')] == cmd_visual.label_style
    assert options[('%1', '@ccb_label_style')] == agent1_visual.label_style
    assert options[('%2', '@ccb_label_style')] == agent2_visual.label_style
    assert options[('%3', '@ccb_label_style')] == agent3_visual.label_style
    assert styles['%1'] == (agent1_visual.border_style, agent1_visual.active_border_style)
    assert styles['%2'] == (agent2_visual.border_style, agent2_visual.active_border_style)
    assert styles['%3'] == (agent3_visual.border_style, agent3_visual.active_border_style)


def test_prepare_tmux_start_layout_materializes_configured_cmd_plus_eight_agents(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-layout-nine-slots'
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    agent_names = tuple(f'agent{index}' for index in range(1, 9))
    # Provider names are rendered into layout_spec only; this test does not launch runtimes.
    providers_by_agent = {
        'agent1': 'codex',
        'agent2': 'claude',
        'agent3': 'fake-codex',
        'agent4': 'fake-claude',
        'agent5': 'fake-gemini',
        'agent6': 'fake-legacy',
        'agent7': 'fake',
        'agent8': 'codex',
    }
    (project_root / '.ccb' / 'ccb.config').write_text(
        'version = 2\n'
        f'default_agents = [{", ".join(f"{name!r}" for name in agent_names)}]\n'
        'cmd_enabled = true\n'
        + ''.join(
            f'\n[agents.{name}]\n'
            f'provider = "{providers_by_agent[name]}"\n'
            'target = "."\n'
            'workspace_mode = "inplace"\n'
            'restore = "auto"\n'
            'permission = "manual"\n'
            for name in agent_names
        ),
        encoding='utf-8',
    )
    ctx = _context(project_root)
    config = load_project_config(project_root).config
    split_ids = iter(f'%{index}' for index in range(1, 9))
    create_calls: list[tuple[str | None, str, int]] = []

    class FakeTmuxBackend:
        def get_current_pane_id(self) -> str:
            return '%0'

        def set_pane_title(self, pane_id: str, title: str) -> None:
            pass

        def set_pane_user_option(self, pane_id: str, name: str, value: str) -> None:
            pass

        def set_pane_style(
            self,
            pane_id: str,
            *,
            border_style: str | None = None,
            active_border_style: str | None = None,
        ) -> None:
            pass

        def create_pane(self, cmd: str, cwd: str, direction: str = 'right', percent: int = 50, parent_pane: str | None = None) -> str:
            del cmd, cwd
            create_calls.append((parent_pane, direction, percent))
            return next(split_ids)

    monkeypatch.setattr(tmux_start_layout, 'TmuxBackend', FakeTmuxBackend)

    layout = tmux_start_layout.prepare_tmux_start_layout(
        ctx,
        config=config,
        targets=agent_names,
    )

    assert layout.cmd_pane_id == '%0'
    # Split ids are assigned in materialization order; left-branch leaves are labeled
    # after their descendant panes have already been created.
    assert config.layout_spec == (
        'cmd, agent1:codex, agent2:claude, agent3:fake-codex, agent4:fake-claude; '
        'agent5:fake-gemini, agent6:fake-legacy, agent7:fake, agent8:codex'
    )
    assert layout.agent_panes == {
        'agent1': '%5',
        'agent2': '%4',
        'agent3': '%3',
        'agent4': '%2',
        'agent5': '%1',
        'agent6': '%8',
        'agent7': '%7',
        'agent8': '%6',
    }
    assert create_calls == [
        ('%0', 'right', 44),
        ('%0', 'bottom', 20),
        ('%0', 'bottom', 25),
        ('%0', 'bottom', 33),
        ('%0', 'bottom', 50),
        ('%1', 'bottom', 25),
        ('%1', 'bottom', 33),
        ('%1', 'bottom', 50),
    ]


def test_prepare_tmux_start_layout_uses_root_pane_for_first_agent_when_cmd_disabled(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-layout-no-cmd'
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('demo:codex\n', encoding='utf-8')
    ctx = _context(project_root)
    config = load_project_config(project_root).config
    calls: list[tuple[str, str, str]] = []

    class FakeTmuxBackend:
        def get_current_pane_id(self) -> str:
            return '%0'

        def set_pane_title(self, pane_id: str, title: str) -> None:
            calls.append(('title', pane_id, title))

        def set_pane_user_option(self, pane_id: str, name: str, value: str) -> None:
            calls.append((name, pane_id, value))

        def create_pane(self, cmd: str, cwd: str, direction: str = 'right', percent: int = 50, parent_pane: str | None = None) -> str:
            raise AssertionError('single-agent no-cmd layout should reuse root pane')

    monkeypatch.setattr(tmux_start_layout, 'TmuxBackend', FakeTmuxBackend)

    layout = tmux_start_layout.prepare_tmux_start_layout(
        ctx,
        config=config,
        targets=('demo',),
    )

    assert layout.cmd_pane_id is None
    assert layout.agent_panes == {'demo': '%0'}
    assert ('title', '%0', 'demo') in calls
