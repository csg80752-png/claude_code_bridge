from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from agents.config_loader import load_project_config


class ProviderHomeSyncResultLike(Protocol):
    path: Path
    synced: tuple[str, ...]


@dataclass(frozen=True)
class ProviderHomeSyncCapability:
    name: str
    status: str
    ownership: str
    detail: str
    schema_version: int = 1


@dataclass(frozen=True)
class ProviderHomeSyncPolicy:
    provider: str
    sentinel_name: str
    sentinel_content: str
    source_home: Callable[[], Path]
    runtime_home: Callable[[Path], Path]
    profile_home: Callable[[Path], Path | None]
    sync_options: Callable[[object | None], dict[str, object]]
    sync_home: Callable[..., ProviderHomeSyncResultLike]
    capabilities: tuple[ProviderHomeSyncCapability, ...] = ()


@dataclass(frozen=True)
class ProviderHomeSyncAgentResult:
    agent_name: str
    path: Path
    synced: tuple[str, ...]
    skipped_auth: bool


@dataclass(frozen=True)
class ProviderHomeSyncSkippedAgent:
    agent_name: str
    reason: str
    path: Path | None = None


@dataclass(frozen=True)
class ProviderHomeSyncSummary:
    source_home: Path
    agents: tuple[ProviderHomeSyncAgentResult, ...]
    skipped: tuple[ProviderHomeSyncSkippedAgent, ...] = ()


def sync_project_provider_homes(
    context,
    policy: ProviderHomeSyncPolicy,
    command=None,
    *,
    config=None,
    source_home: Path | None = None,
) -> ProviderHomeSyncSummary:
    resolved_config = config or load_project_config(context.project.project_root).config
    source = Path(source_home) if source_home is not None else policy.source_home()
    sync_options = policy.sync_options(command)
    provider = str(policy.provider or "").strip().lower()

    results: list[ProviderHomeSyncAgentResult] = []
    skipped: list[ProviderHomeSyncSkippedAgent] = []
    for agent_name, spec in sorted(resolved_config.agents.items()):
        if str(getattr(spec, "provider", "")).strip().lower() != provider:
            continue
        runtime_dir = context.paths.agent_provider_runtime_dir(agent_name, spec.provider)
        profile_home = policy.profile_home(runtime_dir)
        if profile_home is not None:
            skipped.append(ProviderHomeSyncSkippedAgent(agent_name=agent_name, reason="profile-home", path=profile_home))
            continue
        home = policy.runtime_home(runtime_dir)
        if not is_syncable_managed_home(
            home,
            sentinel_name=policy.sentinel_name,
            sentinel_content=policy.sentinel_content,
            ccb_dir=getattr(context.paths, "ccb_dir", None),
            migrate_legacy=True,
        ):
            skipped.append(ProviderHomeSyncSkippedAgent(agent_name=agent_name, reason="unmanaged-home", path=home))
            continue
        result = policy.sync_home(home, source_home=source, **sync_options)
        results.append(
            ProviderHomeSyncAgentResult(
                agent_name=agent_name,
                path=result.path,
                synced=result.synced,
                skipped_auth=bool(getattr(result, "skipped_auth", False)),
            )
        )

    return ProviderHomeSyncSummary(source_home=source, agents=tuple(results), skipped=tuple(skipped))


def is_syncable_managed_home(
    path: Path,
    *,
    sentinel_name: str,
    sentinel_content: str,
    ccb_dir: Path | None,
    migrate_legacy: bool = False,
) -> bool:
    if not path.exists() or not path.is_dir() or path.is_symlink():
        return False
    if has_symlink_between(path, ccb_dir):
        return False
    sentinel = path / sentinel_name
    if sentinel.exists():
        return sentinel.is_file() and not sentinel.is_symlink()
    if not _is_legacy_ccb_runtime_home(path, ccb_dir=ccb_dir):
        return False
    if migrate_legacy:
        sentinel.write_text(sentinel_content, encoding="utf-8")
    return True


def _is_legacy_ccb_runtime_home(path: Path, *, ccb_dir: Path | None) -> bool:
    if ccb_dir is None:
        return False
    try:
        relative = path.relative_to(ccb_dir)
    except ValueError:
        return False
    parts = relative.parts
    return (
        len(parts) == 5
        and parts[0] == "agents"
        and parts[2] == "provider-runtime"
        and parts[4].endswith("-home")
    )


def has_symlink_between(path: Path, ccb_dir: Path | None) -> bool:
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
    "ProviderHomeSyncAgentResult",
    "ProviderHomeSyncCapability",
    "ProviderHomeSyncPolicy",
    "ProviderHomeSyncSkippedAgent",
    "ProviderHomeSyncSummary",
    "has_symlink_between",
    "is_syncable_managed_home",
    "sync_project_provider_homes",
]
