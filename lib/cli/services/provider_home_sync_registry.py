from __future__ import annotations

from collections.abc import Callable

from cli.services.claude_home_sync import claude_home_sync_policy
from cli.services.codex_home_sync import codex_home_sync_policy
from cli.services.provider_home_sync import ProviderHomeSyncPolicy


_POLICY_FACTORIES: dict[str, Callable[[], ProviderHomeSyncPolicy]] = {
    "claude": claude_home_sync_policy,
    "codex": codex_home_sync_policy,
}


def provider_home_sync_policy(provider: str) -> ProviderHomeSyncPolicy | None:
    provider_name = str(provider or "").strip().lower()
    factory = _POLICY_FACTORIES.get(provider_name)
    if factory is None:
        return None
    return factory()


__all__ = ["provider_home_sync_policy"]
