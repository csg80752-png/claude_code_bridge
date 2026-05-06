from __future__ import annotations

from pathlib import Path

from cli.kill_runtime.daemons import find_project_ccbd_daemon_pids, kill_project_ccbd_daemons
import cli.kill_runtime.zombies as zombies


def test_find_all_zombie_sessions_filters_dead_parents() -> None:
    result = zombies.find_all_zombie_sessions(
        is_pid_alive=lambda pid: pid == 456,
        list_tmux_sessions_fn=lambda: [
            'codex-123-worker',
            'claude-456-run',
            'demo-other',
        ],
    )

    assert result == [
        {
            'session': 'codex-123-worker',
            'provider': 'codex',
            'parent_pid': 123,
        }
    ]


def test_kill_global_zombies_reports_partial_failures(capsys) -> None:
    code = zombies.kill_global_zombies(
        yes=True,
        is_pid_alive=lambda pid: False,
        find_all_zombie_sessions_fn=lambda **kwargs: [
            {'session': 'codex-123-worker', 'provider': 'codex', 'parent_pid': 123},
            {'session': 'claude-234-run', 'provider': 'claude', 'parent_pid': 234},
        ],
        kill_tmux_session_fn=lambda name: name == 'codex-123-worker',
    )

    assert code == 0
    out = capsys.readouterr().out
    assert 'Found 2 zombie session(s):' in out
    assert 'Cleaned up 1 zombie session(s), 1 failed' in out


def test_find_project_ccbd_daemon_pids_matches_project_arg(tmp_path: Path) -> None:
    proc = tmp_path / 'proc'
    project = tmp_path / 'workspace'
    other = tmp_path / 'other'
    main = tmp_path / 'install' / 'lib' / 'ccbd' / 'main.py'
    project.mkdir()
    other.mkdir()
    main.parent.mkdir(parents=True)
    main.write_text('', encoding='utf-8')
    for pid, project_arg in ((111, project), (222, other)):
        pid_dir = proc / str(pid)
        pid_dir.mkdir(parents=True)
        (pid_dir / 'cmdline').write_bytes(
            b'\0'.join(
                [
                    b'/usr/bin/python3',
                    str(main).encode(),
                    b'--project',
                    str(project_arg).encode(),
                ]
            )
            + b'\0'
        )

    assert find_project_ccbd_daemon_pids(project_root=project, proc_root=proc) == (111,)


def test_kill_project_ccbd_daemons_terminates_matches(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr('cli.kill_runtime.daemons.find_project_ccbd_daemon_pids', lambda project_root: (111, 222))
    killed: list[int] = []

    result = kill_project_ccbd_daemons(
        project_root=tmp_path,
        terminate_pid_tree_fn=lambda pid: killed.append(pid) or pid == 111,
    )

    assert result == 1
    assert killed == [111, 222]
    assert 'orphan daemon cleanup killed 1 process(es)' in capsys.readouterr().out
