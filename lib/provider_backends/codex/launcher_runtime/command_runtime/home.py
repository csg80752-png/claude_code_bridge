from __future__ import annotations

from pathlib import Path

from provider_backends.codex.launcher_runtime.codex_namespace_isolation import prepare_codex_home_overrides as _prepare


def prepare_codex_home_overrides(runtime_dir: Path, profile) -> dict[str, str]:
    return _prepare(runtime_dir, profile)


__all__ = ['prepare_codex_home_overrides']
