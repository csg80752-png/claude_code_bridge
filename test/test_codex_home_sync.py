from __future__ import annotations

from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from cli.parser import CliParser, CliUsageError
from cli.phase2 import _command_requires_bootstrap_config
from cli.phase2_runtime.handlers_ops import handle_sync_codex_home
from cli.services.codex_home_sync import (
    CodexHomeSyncAgentResult,
    CodexHomeSyncSkippedAgent,
    CodexHomeSyncSummary,
    sync_project_codex_homes,
)
from provider_backends.codex.launcher_runtime.codex_namespace_isolation import (
    _POLICY_FILENAME,
    prepare_codex_isolated_home,
    sync_codex_home_from_source,
    system_codex_home,
)


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _source_home(tmp_path: Path) -> Path:
    source = tmp_path / "source-codex"
    _write(source / "config.toml", 'model = "gpt-5.5"\n')
    _write(source / "auth.json", '{"token":"fresh"}\n')
    _write(source / "skills" / "qa" / "SKILL.md", "qa skill\n")
    _write(source / "commands" / "ship.md", "ship command\n")
    _write(source / "rules" / "default.rules", "allow ls\n")
    _write(source / "sessions" / "rollout.jsonl", "{}\n")
    _write(source / "history.jsonl", "{}\n")
    _write(source / "log" / "codex.log", "log\n")
    _write(source / "logs_2.sqlite", "sqlite\n")
    _write(source / "state_5.sqlite", "state\n")
    _write(source / "shell_snapshots" / "snap.sh", "snap\n")
    _write(source / "tmp" / "x", "tmp\n")
    _write(source / ".tmp" / "x", "tmp\n")
    _write(source / "cache" / "x", "cache\n")
    _write(source / "plugins" / "cache" / "plugin" / "state.json", "{}\n")
    return source


def test_sync_codex_home_refreshes_safe_entries_without_auth_or_runtime_state(tmp_path: Path) -> None:
    source = _source_home(tmp_path)
    target = tmp_path / "runtime" / "codex-home"
    _write(target / "config.toml", 'model = "old"\n')
    _write(target / "auth.json", '{"token":"agent"}\n')
    _write(target / "sessions" / "agent.jsonl", "keep\n")
    _write(target / "history.jsonl", "keep\n")
    _write(target / "skills" / "old" / "SKILL.md", "old\n")

    result = sync_codex_home_from_source(target, source_home=source)

    assert result.synced == ("config.toml", "skills", "commands", "rules")
    assert result.skipped_auth is True
    assert (target / "config.toml").read_text(encoding="utf-8") == 'model = "gpt-5.5"\n'
    assert (target / "skills" / "qa" / "SKILL.md").read_text(encoding="utf-8") == "qa skill\n"
    assert not (target / "skills" / "old").exists()
    assert (target / "commands" / "ship.md").is_file()
    assert (target / "rules" / "default.rules").is_file()
    assert (target / "auth.json").read_text(encoding="utf-8") == '{"token":"agent"}\n'

    assert (target / "sessions" / "agent.jsonl").read_text(encoding="utf-8") == "keep\n"
    assert (target / "history.jsonl").read_text(encoding="utf-8") == "keep\n"
    for unsafe_name in (
        "log",
        "logs_2.sqlite",
        "state_5.sqlite",
        "shell_snapshots",
        "tmp",
        ".tmp",
        "cache",
        "plugins",
    ):
        assert not (target / unsafe_name).exists(), unsafe_name


def test_sync_codex_home_can_include_auth_without_hardlinking_secret_file(tmp_path: Path) -> None:
    source = _source_home(tmp_path)
    target = tmp_path / "runtime" / "codex-home"
    target.mkdir(parents=True)

    result = sync_codex_home_from_source(target, source_home=source, include_auth=True)

    assert "auth.json" in result.synced
    assert (target / "auth.json").read_text(encoding="utf-8") == '{"token":"fresh"}\n'
    assert (target / "auth.json").stat().st_ino != (source / "auth.json").stat().st_ino


def test_prepare_codex_isolated_home_does_not_hardlink_auth_json(tmp_path: Path) -> None:
    source = _source_home(tmp_path)
    runtime_dir = tmp_path / "runtime"

    isolated_home = prepare_codex_isolated_home(runtime_dir, source_home=source)

    assert (isolated_home / "auth.json").read_text(encoding="utf-8") == '{"token":"fresh"}\n'
    assert (isolated_home / "auth.json").stat().st_ino != (source / "auth.json").stat().st_ino


def test_system_codex_home_ignores_ccb_isolated_codex_home_env(tmp_path: Path, monkeypatch) -> None:
    real_home = tmp_path / "home"
    ccb_home = real_home / "project" / ".ccb" / "agents" / "agent1" / "provider-runtime" / "codex" / "codex-home"
    ccb_home.mkdir(parents=True)
    real_codex = real_home / ".codex"
    real_codex.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(real_home))
    monkeypatch.setenv("CODEX_HOME", str(ccb_home))

    assert system_codex_home() == real_codex


def test_system_codex_home_keeps_explicit_non_ccb_codex_home_env(tmp_path: Path, monkeypatch) -> None:
    explicit_home = tmp_path / "operator-codex-home"
    explicit_home.mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(explicit_home))

    assert system_codex_home() == explicit_home


def test_sync_codex_home_does_not_hardlink_safe_entries(tmp_path: Path) -> None:
    source = _source_home(tmp_path)
    target = tmp_path / "runtime" / "codex-home"
    target.mkdir(parents=True)

    result = sync_codex_home_from_source(target, source_home=source)

    assert "config.toml" in result.synced
    assert (target / "config.toml").stat().st_ino != (source / "config.toml").stat().st_ino
    assert (target / "skills" / "qa" / "SKILL.md").stat().st_ino != (
        source / "skills" / "qa" / "SKILL.md"
    ).stat().st_ino


def test_sync_codex_home_include_auth_skips_source_auth_symlink(tmp_path: Path) -> None:
    source = _source_home(tmp_path)
    secret = tmp_path / "not-codex-auth"
    secret.write_text("external secret\n", encoding="utf-8")
    (source / "auth.json").unlink()
    (source / "auth.json").symlink_to(secret)
    target = tmp_path / "runtime" / "codex-home"
    target.mkdir(parents=True)

    result = sync_codex_home_from_source(target, source_home=source, include_auth=True)

    assert "auth.json" not in result.synced
    assert not (target / "auth.json").exists()


def test_sync_codex_home_rejects_symlinked_target_home(tmp_path: Path) -> None:
    source = _source_home(tmp_path)
    external_home = tmp_path / "external"
    _write(external_home / "config.toml", "external\n")
    target = tmp_path / "runtime" / "codex-home"
    target.parent.mkdir(parents=True)
    target.symlink_to(external_home, target_is_directory=True)

    with pytest.raises(ValueError, match="codex home must not be a symlink"):
        sync_codex_home_from_source(target, source_home=source)

    assert (external_home / "config.toml").read_text(encoding="utf-8") == "external\n"


def test_sync_codex_home_rejects_symlinked_policy_sentinel(tmp_path: Path) -> None:
    source = _source_home(tmp_path)
    target = tmp_path / "runtime" / "codex-home"
    external_sentinel = tmp_path / "external-sentinel"
    external_sentinel.write_text("keep\n", encoding="utf-8")
    target.mkdir(parents=True)
    (target / _POLICY_FILENAME).symlink_to(external_sentinel)

    with pytest.raises(ValueError, match="policy sentinel must not be a symlink"):
        sync_codex_home_from_source(target, source_home=source)

    assert external_sentinel.read_text(encoding="utf-8") == "keep\n"


def test_cli_parser_accepts_sync_codex_home_flags() -> None:
    command = CliParser().parse(["sync-codex-home", "--include-auth"])

    assert command.kind == "sync-codex-home"
    assert command.include_auth is True


def test_cli_parser_rejects_sync_codex_home_extra_args() -> None:
    with pytest.raises(CliUsageError):
        CliParser().parse(["sync-codex-home", "agent1"])


def test_sync_codex_home_does_not_bootstrap_missing_config() -> None:
    command = CliParser().parse(["sync-codex-home"])

    assert _command_requires_bootstrap_config(command) is False


class _FakePaths:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.ccb_dir = root / ".ccb"

    def agent_provider_runtime_dir(self, agent_name: str, provider: str) -> Path:
        return self.root / ".ccb" / "agents" / agent_name / "provider-runtime" / provider


def test_project_sync_refreshes_configured_codex_agent_homes_only(tmp_path: Path) -> None:
    source = _source_home(tmp_path)
    paths = _FakePaths(tmp_path / "project")
    codex_home = paths.agent_provider_runtime_dir("agent1", "codex") / "codex-home"
    claude_home = paths.agent_provider_runtime_dir("agent2", "claude") / "codex-home"
    _write(codex_home / _POLICY_FILENAME, "r1\n")
    _write(codex_home / "config.toml", "old\n")

    context = SimpleNamespace(
        paths=paths,
        project=SimpleNamespace(project_root=tmp_path / "project"),
    )
    config = SimpleNamespace(
        agents={
            "agent1": SimpleNamespace(name="agent1", provider="codex"),
            "agent2": SimpleNamespace(name="agent2", provider="claude"),
        }
    )

    summary = sync_project_codex_homes(
        context,
        config=config,
        source_home=source,
    )

    assert tuple(result.agent_name for result in summary.agents) == ("agent1",)
    assert summary.agents[0].synced == ("config.toml", "skills", "commands", "rules")
    assert (codex_home / "config.toml").read_text(encoding="utf-8") == 'model = "gpt-5.5"\n'
    assert not claude_home.exists()


def test_project_sync_does_not_create_missing_codex_home(tmp_path: Path) -> None:
    source = _source_home(tmp_path)
    paths = _FakePaths(tmp_path / "project")
    codex_home = paths.agent_provider_runtime_dir("agent1", "codex") / "codex-home"
    context = SimpleNamespace(
        paths=paths,
        project=SimpleNamespace(project_root=tmp_path / "project"),
    )
    config = SimpleNamespace(
        agents={"agent1": SimpleNamespace(name="agent1", provider="codex")}
    )

    summary = sync_project_codex_homes(context, config=config, source_home=source)

    assert summary.agents == ()
    assert summary.skipped[0].reason == "unmanaged-home"
    assert not codex_home.exists()


def test_project_sync_skips_symlinked_codex_home(tmp_path: Path) -> None:
    source = _source_home(tmp_path)
    paths = _FakePaths(tmp_path / "project")
    codex_home = paths.agent_provider_runtime_dir("agent1", "codex") / "codex-home"
    external_home = tmp_path / "external-codex-home"
    _write(external_home / _POLICY_FILENAME, "r1\n")
    _write(external_home / "config.toml", "external\n")
    codex_home.parent.mkdir(parents=True)
    codex_home.symlink_to(external_home, target_is_directory=True)
    context = SimpleNamespace(
        paths=paths,
        project=SimpleNamespace(project_root=tmp_path / "project"),
    )
    config = SimpleNamespace(
        agents={"agent1": SimpleNamespace(name="agent1", provider="codex")}
    )

    summary = sync_project_codex_homes(context, config=config, source_home=source)

    assert summary.agents == ()
    assert summary.skipped[0].reason == "unmanaged-home"
    assert (external_home / "config.toml").read_text(encoding="utf-8") == "external\n"


def test_project_sync_skips_symlinked_runtime_ancestor(tmp_path: Path) -> None:
    source = _source_home(tmp_path)
    paths = _FakePaths(tmp_path / "project")
    runtime_dir = paths.agent_provider_runtime_dir("agent1", "codex")
    external_runtime = tmp_path / "external-runtime"
    external_home = external_runtime / "codex-home"
    _write(external_home / _POLICY_FILENAME, "r1\n")
    _write(external_home / "config.toml", "external\n")
    runtime_dir.parent.mkdir(parents=True)
    runtime_dir.symlink_to(external_runtime, target_is_directory=True)
    context = SimpleNamespace(
        paths=paths,
        project=SimpleNamespace(project_root=tmp_path / "project"),
    )
    config = SimpleNamespace(
        agents={"agent1": SimpleNamespace(name="agent1", provider="codex")}
    )

    summary = sync_project_codex_homes(context, config=config, source_home=source)

    assert summary.agents == ()
    assert summary.skipped[0].reason == "unmanaged-home"
    assert (external_home / "config.toml").read_text(encoding="utf-8") == "external\n"


def test_project_sync_skips_symlinked_policy_sentinel(tmp_path: Path) -> None:
    source = _source_home(tmp_path)
    paths = _FakePaths(tmp_path / "project")
    codex_home = paths.agent_provider_runtime_dir("agent1", "codex") / "codex-home"
    external_sentinel = tmp_path / "external-sentinel"
    external_sentinel.write_text("keep\n", encoding="utf-8")
    codex_home.mkdir(parents=True)
    (codex_home / _POLICY_FILENAME).symlink_to(external_sentinel)
    context = SimpleNamespace(
        paths=paths,
        project=SimpleNamespace(project_root=tmp_path / "project"),
    )
    config = SimpleNamespace(
        agents={"agent1": SimpleNamespace(name="agent1", provider="codex")}
    )

    summary = sync_project_codex_homes(context, config=config, source_home=source)

    assert summary.agents == ()
    assert summary.skipped[0].reason == "unmanaged-home"
    assert external_sentinel.read_text(encoding="utf-8") == "keep\n"


def test_project_sync_skips_profile_backed_codex_home_with_reason(tmp_path: Path) -> None:
    source = _source_home(tmp_path)
    paths = _FakePaths(tmp_path / "project")
    runtime_dir = paths.agent_provider_runtime_dir("agent1", "codex")
    _write(runtime_dir / "provider-profile.json", '{"provider":"codex","agent_name":"agent1","runtime_home":"/tmp/profile"}\n')
    context = SimpleNamespace(
        paths=paths,
        project=SimpleNamespace(project_root=tmp_path / "project"),
    )
    config = SimpleNamespace(
        agents={"agent1": SimpleNamespace(name="agent1", provider="codex")}
    )

    summary = sync_project_codex_homes(context, config=config, source_home=source)

    assert summary.agents == ()
    assert summary.skipped[0].agent_name == "agent1"
    assert summary.skipped[0].reason == "profile-home"


def test_project_sync_skips_explicit_codex_home_env_with_reason(tmp_path: Path) -> None:
    source = _source_home(tmp_path)
    paths = _FakePaths(tmp_path / "project")
    runtime_dir = paths.agent_provider_runtime_dir("agent1", "codex")
    _write(
        runtime_dir / "provider-profile.json",
        '{"provider":"codex","agent_name":"agent1","env":{"CODEX_HOME":"/tmp/profile-env"}}\n',
    )
    context = SimpleNamespace(
        paths=paths,
        project=SimpleNamespace(project_root=tmp_path / "project"),
    )
    config = SimpleNamespace(
        agents={"agent1": SimpleNamespace(name="agent1", provider="codex")}
    )

    summary = sync_project_codex_homes(context, config=config, source_home=source)

    assert summary.agents == ()
    assert summary.skipped[0].agent_name == "agent1"
    assert summary.skipped[0].reason == "profile-home"


def test_sync_codex_home_handler_renders_stable_agent_summary(tmp_path: Path) -> None:
    command = CliParser().parse(["sync-codex-home"])
    out = StringIO()
    summary = CodexHomeSyncSummary(
        source_home=tmp_path / "source",
        agents=(
            CodexHomeSyncAgentResult(
                agent_name="agent1",
                path=tmp_path / "agent1" / "codex-home",
                synced=("config.toml", "skills"),
                skipped_auth=True,
            ),
        ),
        skipped=(
            CodexHomeSyncSkippedAgent(
                agent_name="agent2",
                reason="profile-home",
                path=tmp_path / "agent2" / "custom-home",
            ),
        ),
    )
    services = SimpleNamespace(
        sync_project_codex_homes=lambda context, parsed: summary,
        write_lines=lambda stream, lines: stream.write("\n".join(lines) + "\n"),
    )

    exit_code = handle_sync_codex_home(SimpleNamespace(), command, out, services)

    assert exit_code == 0
    assert out.getvalue().splitlines() == [
        "command_status: synced",
        f"source_home: {tmp_path / 'source'}",
        "codex_agents: 1",
        f"agent: agent1 synced=config.toml,skills auth=skipped path={tmp_path / 'agent1' / 'codex-home'}",
        f"skipped: agent2 reason=profile-home path={tmp_path / 'agent2' / 'custom-home'}",
    ]


def test_sync_codex_home_handler_reports_noop_for_empty_summary(tmp_path: Path) -> None:
    command = CliParser().parse(["sync-codex-home"])
    out = StringIO()
    summary = CodexHomeSyncSummary(source_home=tmp_path / "source", agents=(), skipped=())
    services = SimpleNamespace(
        sync_project_codex_homes=lambda context, parsed: summary,
        write_lines=lambda stream, lines: stream.write("\n".join(lines) + "\n"),
    )

    exit_code = handle_sync_codex_home(SimpleNamespace(), command, out, services)

    assert exit_code == 0
    assert out.getvalue().splitlines() == [
        "command_status: noop",
        f"source_home: {tmp_path / 'source'}",
        "codex_agents: 0",
    ]


def test_sync_codex_home_handler_reports_skipped_only_noop(tmp_path: Path) -> None:
    command = CliParser().parse(["sync-codex-home"])
    out = StringIO()
    summary = CodexHomeSyncSummary(
        source_home=tmp_path / "source",
        agents=(),
        skipped=(
            CodexHomeSyncSkippedAgent(
                agent_name="agent1",
                reason="profile-home",
                path=tmp_path / "profile-home",
            ),
        ),
    )
    services = SimpleNamespace(
        sync_project_codex_homes=lambda context, parsed: summary,
        write_lines=lambda stream, lines: stream.write("\n".join(lines) + "\n"),
    )

    exit_code = handle_sync_codex_home(SimpleNamespace(), command, out, services)

    assert exit_code == 0
    assert out.getvalue().splitlines() == [
        "command_status: noop",
        f"source_home: {tmp_path / 'source'}",
        "codex_agents: 0",
        f"skipped: agent1 reason=profile-home path={tmp_path / 'profile-home'}",
    ]
