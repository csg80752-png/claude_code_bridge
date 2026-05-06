from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil

from provider_backends.claude.launcher import claude_home_for_runtime
from provider_backends.claude.launcher_runtime.service import _POLICY_FILENAME
from cli.services.provider_home_sync import (
    ProviderHomeSyncAgentResult,
    ProviderHomeSyncPolicy,
    ProviderHomeSyncSkippedAgent,
    ProviderHomeSyncSummary,
    sync_project_provider_homes,
)


_SAFE_ENTRIES = ("settings.json", "commands", "agents", "skills")


@dataclass(frozen=True)
class ClaudeHomeSyncResult:
    path: Path
    synced: tuple[str, ...]


ClaudeHomeSyncAgentResult = ProviderHomeSyncAgentResult
ClaudeHomeSyncSkippedAgent = ProviderHomeSyncSkippedAgent
ClaudeHomeSyncSummary = ProviderHomeSyncSummary


def sync_project_claude_homes(
    context,
    command=None,
    *,
    config=None,
    source_home: Path | None = None,
) -> ClaudeHomeSyncSummary:
    return sync_project_provider_homes(
        context,
        _claude_policy(),
        command,
        config=config,
        source_home=source_home,
    )


def system_claude_config_home() -> Path:
    return Path.home() / ".claude"


def sync_claude_home_from_source(
    claude_home: Path,
    *,
    source_home: Path | None = None,
) -> ClaudeHomeSyncResult:
    target_home = Path(claude_home)
    if target_home.is_symlink():
        raise ValueError("claude home must not be a symlink")
    target_config_home = target_home / ".claude"
    source = Path(source_home) if source_home is not None else system_claude_config_home()

    synced: list[str] = []
    for name in _SAFE_ENTRIES:
        src = source / name
        if not src.exists() or src.is_symlink():
            continue
        if src.is_dir() and _contains_symlink(src):
            continue
        _refresh_physical_entry(src, target_config_home / name)
        synced.append(name)
    return ClaudeHomeSyncResult(path=target_home, synced=tuple(synced))


def _claude_policy() -> ProviderHomeSyncPolicy:
    return ProviderHomeSyncPolicy(
        provider="claude",
        sentinel_name=_POLICY_FILENAME,
        source_home=system_claude_config_home,
        runtime_home=claude_home_for_runtime,
        profile_home=lambda runtime_dir: None,
        sync_options=lambda command: {},
        sync_home=sync_claude_home_from_source,
    )


def _refresh_physical_entry(source: Path, target: Path) -> None:
    _remove_path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_file():
        shutil.copy2(source, target)
    elif source.is_dir():
        shutil.copytree(source, target, symlinks=False)


def _contains_symlink(path: Path) -> bool:
    return any(child.is_symlink() for child in path.rglob("*"))


def _remove_path(path: Path) -> None:
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    except FileNotFoundError:
        return


__all__ = [
    "ClaudeHomeSyncAgentResult",
    "ClaudeHomeSyncResult",
    "ClaudeHomeSyncSkippedAgent",
    "ClaudeHomeSyncSummary",
    "sync_claude_home_from_source",
    "sync_project_claude_homes",
    "system_claude_config_home",
]
