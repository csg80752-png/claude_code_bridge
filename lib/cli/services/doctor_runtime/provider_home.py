from __future__ import annotations

from pathlib import Path

from cli.services.provider_home_sync import is_syncable_managed_home
from provider_backends.claude.launcher import claude_home_for_runtime
from provider_backends.claude.launcher_runtime.service import _POLICY_FILENAME as _CLAUDE_POLICY_FILENAME
from provider_backends.codex.launcher_runtime.codex_namespace_isolation import (
    _POLICY_FILENAME as _CODEX_POLICY_FILENAME,
    explicit_codex_home_overrides,
    isolated_home_for_runtime,
)
from provider_profiles import load_resolved_provider_profile


def enrich_provider_home_sync_status(context, *, config, agents: list[dict]) -> list[dict]:
    enabled = set(getattr(config, "provider_home_sync", ()) or ())
    specs = dict(getattr(config, "agents", {}) or {})
    return [
        {
            **agent,
            **_provider_home_sync_status(
                context,
                spec=specs.get(str(agent.get("agent_name") or "")),
                enabled=enabled,
            ),
        }
        for agent in agents
    ]


def _provider_home_sync_status(context, *, spec, enabled: set[str]) -> dict[str, object]:
    provider = str(getattr(spec, "provider", "") or "").strip().lower()
    sync_enabled = provider in enabled
    home, sentinel = _provider_home(context, spec=spec, provider=provider)
    if provider not in {"codex", "claude"}:
        reason = "unsupported"
    elif not sync_enabled:
        reason = "not-enabled"
    elif provider == "codex" and _codex_profile_home(context, spec=spec) is not None:
        home = _codex_profile_home(context, spec=spec)
        reason = "profile-home"
    elif home is None or sentinel is None:
        reason = "unsupported"
    elif is_syncable_managed_home(home, sentinel_name=sentinel, ccb_dir=context.paths.ccb_dir):
        reason = "managed"
    else:
        reason = "unmanaged-home"
    return {
        "provider_home_sync_enabled": sync_enabled,
        "provider_home_sync_home": str(home) if home is not None else None,
        "provider_home_sync_managed": reason == "managed",
        "provider_home_sync_reason": reason,
    }


def _provider_home(context, *, spec, provider: str) -> tuple[Path | None, str | None]:
    if spec is None:
        return None, None
    runtime_dir = context.paths.agent_provider_runtime_dir(spec.name, provider)
    if provider == "codex":
        return isolated_home_for_runtime(runtime_dir), _CODEX_POLICY_FILENAME
    if provider == "claude":
        return claude_home_for_runtime(runtime_dir), _CLAUDE_POLICY_FILENAME
    return None, None


def _codex_profile_home(context, *, spec) -> Path | None:
    if spec is None:
        return None
    runtime_dir = context.paths.agent_provider_runtime_dir(spec.name, "codex")
    profile = load_resolved_provider_profile(runtime_dir)
    if profile is None:
        return None
    if getattr(profile, "runtime_home", None):
        return Path(str(profile.runtime_home)).expanduser()
    env_home = explicit_codex_home_overrides(getattr(profile, "env", {})).get("CODEX_HOME")
    if env_home:
        return Path(env_home).expanduser()
    return None


__all__ = ["enrich_provider_home_sync_status"]
