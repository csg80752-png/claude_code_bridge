from __future__ import annotations

from types import SimpleNamespace

from agents.config_loader import ConfigValidationError, load_project_config

from .claude_home_sync import sync_project_claude_homes
from .codex_home_sync import sync_project_codex_homes


def sync_provider_home_before_launch(context, spec, runtime_dir) -> None:
    del runtime_dir
    try:
        config = load_project_config(context.project.project_root).config
    except ConfigValidationError:
        return
    enabled = set(getattr(config, "provider_home_sync", ()) or ())
    provider = str(getattr(spec, "provider", "") or "").strip().lower()
    if provider not in enabled:
        return
    scoped_config = SimpleNamespace(agents={spec.name: spec})
    if provider == "codex":
        sync_project_codex_homes(context, config=scoped_config)
    elif provider == "claude":
        sync_project_claude_homes(context, config=scoped_config)


__all__ = ["sync_provider_home_before_launch"]
