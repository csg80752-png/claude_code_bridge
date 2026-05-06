from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agents.config_loader import load_project_config
from provider_backends.codex.launcher_runtime.codex_namespace_isolation import (
    _POLICY_FILENAME,
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
    for agent_name, spec in sorted(resolved_config.agents.items()):
        if str(getattr(spec, "provider", "")).strip().lower() != "codex":
            continue
        runtime_dir = context.paths.agent_provider_runtime_dir(agent_name, spec.provider)
        isolated_home = isolated_home_for_runtime(runtime_dir)
        if not _is_syncable_managed_codex_home(isolated_home):
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

    return CodexHomeSyncSummary(source_home=source, agents=tuple(results))


def _is_syncable_managed_codex_home(path: Path) -> bool:
    return path.exists() and path.is_dir() and not path.is_symlink() and (path / _POLICY_FILENAME).is_file()


__all__ = [
    "CodexHomeSyncAgentResult",
    "CodexHomeSyncSummary",
    "sync_project_codex_homes",
]
