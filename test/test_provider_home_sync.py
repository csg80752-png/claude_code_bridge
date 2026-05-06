from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from cli.services.provider_home_sync import (
    ProviderHomeSyncPolicy,
    sync_project_provider_homes,
)


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class _FakePaths:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.ccb_dir = root / ".ccb"

    def agent_provider_runtime_dir(self, agent_name: str, provider: str) -> Path:
        return self.ccb_dir / "agents" / agent_name / "provider-runtime" / provider


@dataclass(frozen=True)
class _SyncResult:
    path: Path
    synced: tuple[str, ...]
    skipped_auth: bool


def _policy(tmp_path: Path, *, profile_home: Path | None = None) -> ProviderHomeSyncPolicy:
    source = tmp_path / "source"
    _write(source / "config", "fresh\n")

    def runtime_home(runtime_dir: Path) -> Path:
        return runtime_dir / "fake-home"

    def sync_home(home: Path, *, source_home: Path, **options: object) -> _SyncResult:
        assert options == {}
        _write(home / "config", (source_home / "config").read_text(encoding="utf-8"))
        return _SyncResult(path=home, synced=("config",), skipped_auth=False)

    return ProviderHomeSyncPolicy(
        provider="fake",
        sentinel_name=".managed",
        source_home=lambda: source,
        runtime_home=runtime_home,
        profile_home=lambda runtime_dir: profile_home,
        sync_options=lambda command: {},
        sync_home=sync_home,
    )


def _context(tmp_path: Path):
    return SimpleNamespace(
        paths=_FakePaths(tmp_path / "project"),
        project=SimpleNamespace(project_root=tmp_path / "project"),
    )


def test_provider_home_sync_syncs_only_matching_managed_provider_homes(tmp_path: Path) -> None:
    context = _context(tmp_path)
    fake_home = context.paths.agent_provider_runtime_dir("agent1", "fake") / "fake-home"
    other_home = context.paths.agent_provider_runtime_dir("agent2", "other") / "fake-home"
    _write(fake_home / ".managed", "r1\n")
    _write(fake_home / "config", "old\n")
    config = SimpleNamespace(
        agents={
            "agent1": SimpleNamespace(provider="fake"),
            "agent2": SimpleNamespace(provider="other"),
        }
    )

    summary = sync_project_provider_homes(context, _policy(tmp_path), config=config)

    assert tuple(result.agent_name for result in summary.agents) == ("agent1",)
    assert summary.agents[0].synced == ("config",)
    assert fake_home.joinpath("config").read_text(encoding="utf-8") == "fresh\n"
    assert not other_home.exists()


def test_provider_home_sync_skips_missing_home_without_creating_it(tmp_path: Path) -> None:
    context = _context(tmp_path)
    fake_home = context.paths.agent_provider_runtime_dir("agent1", "fake") / "fake-home"
    config = SimpleNamespace(agents={"agent1": SimpleNamespace(provider="fake")})

    summary = sync_project_provider_homes(context, _policy(tmp_path), config=config)

    assert summary.agents == ()
    assert summary.skipped[0].reason == "unmanaged-home"
    assert summary.skipped[0].path == fake_home
    assert not fake_home.exists()


def test_provider_home_sync_skips_profile_backed_home(tmp_path: Path) -> None:
    context = _context(tmp_path)
    profile_home = tmp_path / "profile-home"
    config = SimpleNamespace(agents={"agent1": SimpleNamespace(provider="fake")})

    summary = sync_project_provider_homes(context, _policy(tmp_path, profile_home=profile_home), config=config)

    assert summary.agents == ()
    assert summary.skipped[0].agent_name == "agent1"
    assert summary.skipped[0].reason == "profile-home"
    assert summary.skipped[0].path == profile_home


def test_provider_home_sync_skips_symlinked_runtime_ancestor(tmp_path: Path) -> None:
    context = _context(tmp_path)
    runtime_dir = context.paths.agent_provider_runtime_dir("agent1", "fake")
    external_runtime = tmp_path / "external-runtime"
    external_home = external_runtime / "fake-home"
    _write(external_home / ".managed", "r1\n")
    _write(external_home / "config", "external\n")
    runtime_dir.parent.mkdir(parents=True)
    runtime_dir.symlink_to(external_runtime, target_is_directory=True)
    config = SimpleNamespace(agents={"agent1": SimpleNamespace(provider="fake")})

    summary = sync_project_provider_homes(context, _policy(tmp_path), config=config)

    assert summary.agents == ()
    assert summary.skipped[0].reason == "unmanaged-home"
    assert external_home.joinpath("config").read_text(encoding="utf-8") == "external\n"
