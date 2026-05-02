"""Reference discovery contract for the shell `sample_hot_files()` helper.

The runtime helper must:
  1. Discover `.ccb/agents/<agent>/runtime.json` files for every configured agent.
  2. Discover `.ccb/ccbd/keeper.json` and `.ccb/ccbd/lease.json` if they exist.
  3. Fail loud (non-zero exit / NO_HEARTBEAT_FILES sentinel) when no heartbeat files exist.

This Python reference mirrors that logic so the test enforces the contract.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def discover_heartbeat_files(project_root: Path) -> list[Path]:
    """Return canonical heartbeat-file paths under `project_root/.ccb/`.

    Always returns the same ordering so callers can diff snapshots reliably.
    Raises FileNotFoundError if no heartbeat files exist (fail-loud sentinel).
    """
    if not project_root.exists():
        raise FileNotFoundError(f'project root does not exist: {project_root}')

    found: list[Path] = []
    agents_dir = project_root / '.ccb' / 'agents'
    if agents_dir.is_dir():
        for runtime_path in sorted(agents_dir.glob('*/runtime.json')):
            if runtime_path.is_file():
                found.append(runtime_path)
    ccbd_dir = project_root / '.ccb' / 'ccbd'
    for name in ('keeper.json', 'lease.json'):
        candidate = ccbd_dir / name
        if candidate.is_file():
            found.append(candidate)

    if not found:
        raise FileNotFoundError(f'no heartbeat files under {project_root}/.ccb')
    return found


def _populate(root: Path, *, agents: list[str], keeper: bool, lease: bool) -> None:
    for agent in agents:
        agent_dir = root / '.ccb' / 'agents' / agent
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / 'runtime.json').write_text('{}')
    ccbd_dir = root / '.ccb' / 'ccbd'
    ccbd_dir.mkdir(parents=True, exist_ok=True)
    if keeper:
        (ccbd_dir / 'keeper.json').write_text('{}')
    if lease:
        (ccbd_dir / 'lease.json').write_text('{}')


def test_discovers_runtime_keeper_and_lease(tmp_path: Path) -> None:
    _populate(tmp_path, agents=['agent1'], keeper=True, lease=True)
    files = discover_heartbeat_files(tmp_path)
    names = sorted(f.name for f in files)
    assert names == sorted(['runtime.json', 'keeper.json', 'lease.json'])


def test_discovers_multiple_agents(tmp_path: Path) -> None:
    _populate(tmp_path, agents=['agent1', 'agent2', 'cmd'], keeper=True, lease=True)
    files = discover_heartbeat_files(tmp_path)
    runtime_files = [f for f in files if f.name == 'runtime.json']
    assert len(runtime_files) == 3, f'expected 3 runtime.json discoveries; got {len(runtime_files)}'


def test_no_heartbeat_files_fails_loud(tmp_path: Path) -> None:
    (tmp_path / '.ccb').mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        discover_heartbeat_files(tmp_path)


def test_missing_project_root_fails_loud(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        discover_heartbeat_files(tmp_path / 'nonexistent')


def test_only_keeper_present_succeeds(tmp_path: Path) -> None:
    _populate(tmp_path, agents=[], keeper=True, lease=False)
    files = discover_heartbeat_files(tmp_path)
    assert [f.name for f in files] == ['keeper.json']


def test_excludes_non_runtime_json_under_agents(tmp_path: Path) -> None:
    """Discovery only matches runtime.json — other JSON files like agent.json don't count."""
    agent_dir = tmp_path / '.ccb' / 'agents' / 'agent1'
    agent_dir.mkdir(parents=True)
    (agent_dir / 'agent.json').write_text('{}')  # spec, not runtime
    (agent_dir / 'restore.json').write_text('{}')
    _populate(tmp_path, agents=[], keeper=True, lease=True)
    files = discover_heartbeat_files(tmp_path)
    names = [f.name for f in files]
    assert names.count('runtime.json') == 0
    assert 'keeper.json' in names
    assert 'lease.json' in names


def test_canonical_ordering(tmp_path: Path) -> None:
    """Stable ordering: sorted runtime.json then keeper.json then lease.json."""
    _populate(tmp_path, agents=['cmd', 'agent1', 'agent2'], keeper=True, lease=True)
    files = discover_heartbeat_files(tmp_path)
    runtime_paths = [str(f) for f in files if f.name == 'runtime.json']
    assert runtime_paths == sorted(runtime_paths)
    keeper_idx = next(i for i, f in enumerate(files) if f.name == 'keeper.json')
    lease_idx = next(i for i, f in enumerate(files) if f.name == 'lease.json')
    last_runtime_idx = max(i for i, f in enumerate(files) if f.name == 'runtime.json')
    assert last_runtime_idx < keeper_idx < lease_idx
