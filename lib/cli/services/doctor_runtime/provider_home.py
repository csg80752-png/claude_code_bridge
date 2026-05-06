from __future__ import annotations

from cli.services.provider_home_sync import is_syncable_managed_home
from cli.services.provider_home_sync_registry import provider_home_sync_policy


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
    policy = provider_home_sync_policy(provider)
    runtime_dir = context.paths.agent_provider_runtime_dir(spec.name, provider) if spec is not None else None
    home = policy.runtime_home(runtime_dir) if policy is not None and runtime_dir is not None else None
    profile_home = policy.profile_home(runtime_dir) if policy is not None and runtime_dir is not None else None
    if policy is None:
        reason = "unsupported"
    elif not sync_enabled:
        reason = "not-enabled"
    elif profile_home is not None:
        home = profile_home
        reason = "profile-home"
    elif home is None:
        reason = "unsupported"
    elif is_syncable_managed_home(
        home,
        sentinel_name=policy.sentinel_name,
        sentinel_content=policy.sentinel_content,
        ccb_dir=context.paths.ccb_dir,
    ):
        reason = "managed"
    else:
        reason = "unmanaged-home"
    return {
        "provider_home_sync_enabled": sync_enabled,
        "provider_home_sync_home": str(home) if home is not None else None,
        "provider_home_sync_managed": reason == "managed",
        "provider_home_sync_reason": reason,
        "provider_home_sync_capabilities": _provider_home_sync_capabilities(policy, enabled=sync_enabled),
    }


def _provider_home_sync_capabilities(policy, *, enabled: bool) -> tuple[dict[str, object], ...]:
    if policy is None:
        return ()
    return tuple(
        {
            "schema_version": capability.schema_version,
            "name": capability.name,
            "status": capability.status if enabled else "disabled",
            "ownership": capability.ownership,
            "detail": capability.detail,
        }
        for capability in policy.capabilities
    )


__all__ = ["enrich_provider_home_sync_status"]
