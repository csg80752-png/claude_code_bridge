from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import shutil

from provider_backends.claude.launcher import claude_home_for_runtime
from provider_backends.claude.launcher_runtime.service import _POLICY_FILENAME, _POLICY_VERSION
from cli.services.provider_home_sync import (
    ProviderHomeSyncAgentResult,
    ProviderHomeSyncCapability,
    ProviderHomeSyncPolicy,
    ProviderHomeSyncSkippedAgent,
    ProviderHomeSyncSummary,
    sync_project_provider_homes,
)


_SAFE_ENTRIES = ("settings.json", "CLAUDE.md", "commands", "agents", "skills")
_LOG = logging.getLogger(__name__)


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


def claude_home_sync_policy() -> ProviderHomeSyncPolicy:
    return _claude_policy()


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
        _refresh_physical_entry(src, target_config_home / name)
        synced.append(name)
    return ClaudeHomeSyncResult(path=target_home, synced=tuple(synced))


def _claude_policy() -> ProviderHomeSyncPolicy:
    return ProviderHomeSyncPolicy(
        provider="claude",
        sentinel_name=_POLICY_FILENAME,
        sentinel_content=_POLICY_VERSION + "\n",
        source_home=system_claude_config_home,
        runtime_home=claude_home_for_runtime,
        profile_home=lambda runtime_dir: None,
        sync_options=lambda command: {},
        sync_home=sync_claude_home_from_source,
        capabilities=(
            ProviderHomeSyncCapability(
                name="home_sync",
                status="enabled",
                ownership="managed",
                detail="syncs selected Claude config entries into the managed isolated home",
            ),
            ProviderHomeSyncCapability(
                name="credential_lifecycle",
                status="observed",
                ownership="observed",
                detail="does not copy Claude OAuth credentials",
            ),
            ProviderHomeSyncCapability(
                name="mcp_registration",
                status="external",
                ownership="external",
                detail="MCP registration follows synced Claude settings",
            ),
            ProviderHomeSyncCapability(
                name="instruction_provenance",
                status="enabled",
                ownership="managed",
                detail="CLAUDE.md remains Claude-specific instruction context",
            ),
        ),
    )


def _refresh_physical_entry(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if _physical_entry_matches(source, target):
        return
    if source.is_file():
        staging_file = _staging_path(target)
        _remove_path(staging_file)
        try:
            shutil.copy2(source, staging_file)
            os.replace(staging_file, target)
        finally:
            _remove_path(staging_file)
        return
    if source.is_dir():
        staging_dir = _staging_path(target)
        backup_dir = _backup_path(target)
        _remove_path(staging_dir)
        _remove_path(backup_dir)
        try:
            _copy_physical_tree(source, staging_dir)
            if target.exists() or target.is_symlink():
                os.replace(target, backup_dir)
            os.replace(staging_dir, target)
        except Exception:
            if backup_dir.exists() and not target.exists():
                os.replace(backup_dir, target)
            raise
        finally:
            _remove_path(staging_dir)
            _remove_path(backup_dir)


def _copy_physical_tree(source: Path, target: Path) -> None:
    target.mkdir(parents=True)
    for child in source.iterdir():
        child_target = target / child.name
        if child.is_symlink():
            resolved = child.resolve(strict=False)
            if resolved.is_file():
                shutil.copy2(resolved, child_target)
            else:
                _LOG.warning("Claude home sync skipped broken symlink: %s", child)
            continue
        if child.is_dir():
            _copy_physical_tree(child, child_target)
        elif child.is_file():
            shutil.copy2(child, child_target)


def _physical_entry_matches(source: Path, target: Path) -> bool:
    if source.is_file():
        return (
            target.is_file()
            and not target.is_symlink()
            and _file_signature(source) == _file_signature(target)
        )
    if source.is_dir():
        return (
            target.is_dir()
            and not target.is_symlink()
            and _tree_snapshot(source, source_tree=True)
            == _tree_snapshot(target, source_tree=False)
        )
    return False


def _tree_snapshot(root: Path, *, source_tree: bool) -> tuple[tuple[str, str, int, int], ...]:
    snapshot: list[tuple[str, str, int, int]] = []
    _collect_tree_snapshot(root, root, snapshot, source_tree=source_tree)
    return tuple(snapshot)


def _collect_tree_snapshot(
    root: Path,
    current: Path,
    snapshot: list[tuple[str, str, int, int]],
    *,
    source_tree: bool,
) -> None:
    for child in sorted(current.iterdir(), key=lambda path: path.name):
        rel_path = child.relative_to(root).as_posix()
        if child.is_symlink():
            if not source_tree:
                snapshot.append((rel_path, "symlink", 0, 0))
                continue
            resolved = child.resolve(strict=False)
            if resolved.is_file():
                size, mtime_ns = _file_signature(resolved)
                snapshot.append((rel_path, "file", size, mtime_ns))
            continue
        if child.is_dir():
            snapshot.append((rel_path, "dir", 0, 0))
            _collect_tree_snapshot(root, child, snapshot, source_tree=source_tree)
        elif child.is_file():
            size, mtime_ns = _file_signature(child)
            snapshot.append((rel_path, "file", size, mtime_ns))


def _file_signature(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def _staging_path(target: Path) -> Path:
    return target.with_name(f".{target.name}.ccb-sync-tmp-{os.getpid()}")


def _backup_path(target: Path) -> Path:
    return target.with_name(f".{target.name}.ccb-sync-old-{os.getpid()}")


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
    "claude_home_sync_policy",
    "sync_claude_home_from_source",
    "sync_project_claude_homes",
    "system_claude_config_home",
]
