from __future__ import annotations

from pathlib import Path

from provider_profiles import load_resolved_provider_profile
from provider_backends.codex.launcher_runtime.codex_namespace_isolation import (
    _POLICY_FILENAME,
    _POLICY_VERSION,
    explicit_codex_home_overrides,
    isolated_home_for_runtime,
    sync_codex_home_from_source,
    system_codex_home,
)
from cli.services.provider_home_sync import (
    ProviderHomeSyncAgentResult,
    ProviderHomeSyncPolicy,
    ProviderHomeSyncSkippedAgent,
    ProviderHomeSyncSummary,
    sync_project_provider_homes,
)


CodexHomeSyncAgentResult = ProviderHomeSyncAgentResult
CodexHomeSyncSkippedAgent = ProviderHomeSyncSkippedAgent
CodexHomeSyncSummary = ProviderHomeSyncSummary


def sync_project_codex_homes(
    context,
    command=None,
    *,
    config=None,
    source_home: Path | None = None,
) -> CodexHomeSyncSummary:
    return sync_project_provider_homes(
        context,
        _codex_policy(),
        command,
        config=config,
        source_home=source_home,
    )


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


def _codex_policy() -> ProviderHomeSyncPolicy:
    return ProviderHomeSyncPolicy(
        provider="codex",
        sentinel_name=_POLICY_FILENAME,
        sentinel_content=_POLICY_VERSION + "\n",
        source_home=system_codex_home,
        runtime_home=isolated_home_for_runtime,
        profile_home=_profile_codex_home,
        sync_options=lambda command: {"include_auth": bool(getattr(command, "include_auth", False))},
        sync_home=sync_codex_home_from_source,
    )


__all__ = [
    "CodexHomeSyncAgentResult",
    "CodexHomeSyncSkippedAgent",
    "CodexHomeSyncSummary",
    "sync_project_codex_homes",
]
