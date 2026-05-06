from __future__ import annotations

from agents.config_loader import load_project_config
from provider_core.catalog import build_default_provider_catalog
from provider_execution.registry import build_default_execution_registry

from .daemon import ping_local_state
from .doctor_runtime import agent_summaries, ccbd_summary, doctor_stores, installation_summary, requirements_summary
from .doctor_runtime.provider_home import enrich_provider_home_sync_status


def doctor_summary(context) -> dict:
    config = load_project_config(context.project.project_root).config
    stores = doctor_stores(context)
    catalog = build_default_provider_catalog()
    execution_registry = build_default_execution_registry()
    local = ping_local_state(context)
    errors: list[str] = []
    agents = agent_summaries(
        context,
        config=config,
        stores=stores,
        catalog=catalog,
        execution_registry=execution_registry,
        errors=errors,
    )
    enabled_provider_home_sync = tuple(getattr(config, 'provider_home_sync', ()) or ())
    agents = enrich_provider_home_sync_status(context, config=config, agents=agents)
    return {
        'project': str(context.project.project_root),
        'project_id': context.project.project_id,
        'provider_home_sync_enabled': enabled_provider_home_sync,
        'installation': installation_summary(),
        'requirements': requirements_summary(),
        'ccbd': ccbd_summary(local=local, stores=stores, errors=errors),
        'agents': agents,
    }
