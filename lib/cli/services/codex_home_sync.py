from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agents.config_loader import load_project_config
from provider_profiles import load_resolved_provider_profile
from provider_backends.codex.launcher_runtime.codex_namespace_isolation import (
    _POLICY_FILENAME,
    explicit_codex_home_overrides,
    isolated_home_for_runtime,
    sync_codex_home_from_source,
    system_codex_home,
)


@dataclass(frozen=True)
class CodexHomeSyncAgentResult:
    agent_name: str
    path: Path
    synced: tuple[str, ...]
    skipped_auth: bool


@dataclass(frozen=True)
class CodexHomeSyncSummary:
    source_home: Path
    agents: tuple[CodexHomeSyncAgentResult, ...]
    skipped: tuple["CodexHomeSyncSkippedAgent", ...] = ()


@dataclass(frozen=True)
class CodexHomeSyncSkippedAgent:
    agent_name: str
    reason: str
    path: Path | None = None


def sync_project_codex_homes(
    context,
    command=None,
    *,
    config=None,
    source_home: Path | None = None,
) -> CodexHomeSyncSummary:
    resolved_config = config or load_project_config(context.project.project_root).config
    source = Path(source_home) if source_home is not None else system_codex_home()
    include_auth = bool(getattr(command, "include_auth", False))

    results: list[CodexHomeSyncAgentResult] = []
    skipped: list[CodexHomeSyncSkippedAgent] = []
    for agent_name, spec in sorted(resolved_config.agents.items()):
        if str(getattr(spec, "provider", "")).strip().lower() != "codex":
            continue
        runtime_dir = context.paths.agent_provider_runtime_dir(agent_name, spec.provider)
        profile_home = _profile_codex_home(runtime_dir)
        if profile_home is not None:
            skipped.append(CodexHomeSyncSkippedAgent(agent_name=agent_name, reason="profile-home", path=profile_home))
            continue
        isolated_home = isolated_home_for_runtime(runtime_dir)
        if not _is_syncable_managed_codex_home(isolated_home, ccb_dir=getattr(context.paths, "ccb_dir", None)):
            skipped.append(CodexHomeSyncSkippedAgent(agent_name=agent_name, reason="unmanaged-home", path=isolated_home))
            continue
        result = sync_codex_home_from_source(
            isolated_home,
            source_home=source,
            include_auth=include_auth,
        )
        results.append(
            CodexHomeSyncAgentResult(
                agent_name=agent_name,
                path=result.path,
                synced=result.synced,
                skipped_auth=result.skipped_auth,
            )
        )

    return CodexHomeSyncSummary(source_home=source, agents=tuple(results), skipped=tuple(skipped))


def _profile_codex_home(runtime_dir: Path) -> Path | None:
    profile = load_resolved_provider_profile(runtime_dir)
    if profile is None:
        return None
    if getattr(profile, "runtime_home", None):
        return Path(str(profile.runtime_home)).expanduser()
    env_home = explicit_codex_home_overrides(getattr(profile, "env", {})).get("CODEX_HOME")
    if env_home:
        return Path(env_home).expanduser()
    return None


def _is_syncable_managed_codex_home(path: Path, *, ccb_dir: Path | None) -> bool:
    if not path.exists() or not path.is_dir() or path.is_symlink():
        return False
    if _has_symlink_between(path, ccb_dir):
        return False
    sentinel = path / _POLICY_FILENAME
    return sentinel.exists() and sentinel.is_file() and not sentinel.is_symlink()


def _has_symlink_between(path: Path, ccb_dir: Path | None) -> bool:
    if ccb_dir is None:
        candidates = (path, *path.parents)
    else:
        try:
            relative = path.relative_to(ccb_dir)
        except ValueError:
            return True
        current = ccb_dir
        candidates = [current]
        for part in relative.parts:
            current = current / part
            candidates.append(current)
    return any(candidate.is_symlink() for candidate in candidates)


__all__ = [
    "CodexHomeSyncAgentResult",
    "CodexHomeSyncSkippedAgent",
    "CodexHomeSyncSummary",
    "sync_project_codex_homes",
]
