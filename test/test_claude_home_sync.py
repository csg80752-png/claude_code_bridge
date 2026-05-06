from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from cli.services.claude_home_sync import sync_project_claude_homes
from provider_backends.claude.launcher import claude_namespace_env


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _source_claude_dir(tmp_path: Path) -> Path:
    source = tmp_path / "source-claude"
    _write(source / "settings.json", '{"theme":"dark"}\n')
    _write(source / "commands" / "ship.md", "ship\n")
    _write(source / "agents" / "reviewer.md", "reviewer\n")
    _write(source / "skills" / "qa" / "SKILL.md", "qa\n")
    _write(source / "projects" / "repo" / "session.jsonl", "{}\n")
    _write(source / "session-env" / "sess" / "env.json", "{}\n")
    _write(source / "logs" / "claude.log", "log\n")
    _write(source / "cache" / "x", "cache\n")
    _write(source / "tmp" / "x", "tmp\n")
    _write(source / "auth.json", '{"secret":true}\n')
    return source


class _FakePaths:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.ccb_dir = root / ".ccb"

    def agent_provider_runtime_dir(self, agent_name: str, provider: str) -> Path:
        return self.ccb_dir / "agents" / agent_name / "provider-runtime" / provider


def _context(tmp_path: Path):
    return SimpleNamespace(
        paths=_FakePaths(tmp_path / "project"),
        project=SimpleNamespace(project_root=tmp_path / "project"),
    )


def test_claude_home_sync_refreshes_safe_entries_without_runtime_state(tmp_path: Path) -> None:
    source = _source_claude_dir(tmp_path)
    context = _context(tmp_path)
    runtime_dir = context.paths.agent_provider_runtime_dir("agent1", "claude")
    namespace = claude_namespace_env(runtime_dir)
    claude_home = Path(namespace["HOME"])
    target_claude_dir = claude_home / ".claude"
    _write(target_claude_dir / "settings.json", '{"theme":"old"}\n')
    _write(target_claude_dir / "projects" / "repo" / "existing.jsonl", "keep\n")
    config = SimpleNamespace(agents={"agent1": SimpleNamespace(provider="claude")})

    summary = sync_project_claude_homes(context, config=config, source_home=source)

    assert tuple(result.agent_name for result in summary.agents) == ("agent1",)
    assert summary.agents[0].synced == ("settings.json", "commands", "agents", "skills")
    assert (target_claude_dir / "settings.json").read_text(encoding="utf-8") == '{"theme":"dark"}\n'
    assert (target_claude_dir / "commands" / "ship.md").read_text(encoding="utf-8") == "ship\n"
    assert (target_claude_dir / "agents" / "reviewer.md").is_file()
    assert (target_claude_dir / "skills" / "qa" / "SKILL.md").is_file()
    assert (target_claude_dir / "projects" / "repo" / "existing.jsonl").read_text(encoding="utf-8") == "keep\n"
    assert not (target_claude_dir / "session-env" / "sess" / "env.json").exists()
    for unsafe_name in ("logs", "cache", "tmp", "auth.json"):
        assert not (target_claude_dir / unsafe_name).exists(), unsafe_name


def test_claude_home_sync_skips_unmanaged_home(tmp_path: Path) -> None:
    source = _source_claude_dir(tmp_path)
    context = _context(tmp_path)
    runtime_dir = context.paths.agent_provider_runtime_dir("agent1", "claude")
    claude_home = runtime_dir / "claude-home"
    _write(claude_home / ".claude" / "settings.json", "{}\n")
    config = SimpleNamespace(agents={"agent1": SimpleNamespace(provider="claude")})

    summary = sync_project_claude_homes(context, config=config, source_home=source)

    assert summary.agents == ()
    assert summary.skipped[0].reason == "unmanaged-home"


def test_claude_home_sync_skips_source_symlinked_safe_entry(tmp_path: Path) -> None:
    source = _source_claude_dir(tmp_path)
    external = tmp_path / "external-settings"
    external.write_text('{"external":true}\n', encoding="utf-8")
    (source / "settings.json").unlink()
    (source / "settings.json").symlink_to(external)
    context = _context(tmp_path)
    runtime_dir = context.paths.agent_provider_runtime_dir("agent1", "claude")
    namespace = claude_namespace_env(runtime_dir)
    target_claude_dir = Path(namespace["HOME"]) / ".claude"
    config = SimpleNamespace(agents={"agent1": SimpleNamespace(provider="claude")})

    summary = sync_project_claude_homes(context, config=config, source_home=source)

    assert "settings.json" not in summary.agents[0].synced
    assert not (target_claude_dir / "settings.json").exists()


def test_claude_home_sync_skips_safe_directory_with_nested_symlink(tmp_path: Path) -> None:
    source = _source_claude_dir(tmp_path)
    external = tmp_path / "external-command.md"
    external.write_text("external\n", encoding="utf-8")
    (source / "commands" / "external.md").symlink_to(external)
    context = _context(tmp_path)
    runtime_dir = context.paths.agent_provider_runtime_dir("agent1", "claude")
    namespace = claude_namespace_env(runtime_dir)
    target_claude_dir = Path(namespace["HOME"]) / ".claude"
    config = SimpleNamespace(agents={"agent1": SimpleNamespace(provider="claude")})

    summary = sync_project_claude_homes(context, config=config, source_home=source)

    assert "commands" not in summary.agents[0].synced
    assert not (target_claude_dir / "commands").exists()
