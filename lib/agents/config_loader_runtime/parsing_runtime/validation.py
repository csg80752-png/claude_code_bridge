from __future__ import annotations

from pathlib import Path
from typing import Any

from agents.models import AgentValidationError, ProjectConfig, normalize_agent_name

from ..common import ALLOWED_TOP_LEVEL_KEYS, ConfigValidationError
from .agent_specs import parse_agents
from .expectations import expect_bool, expect_string, expect_string_list


def validate_project_config(document: dict[str, Any], *, source_path: Path | None = None) -> ProjectConfig:
    _validate_document_shape(document)
    default_agents = _parse_default_agents(document)
    parsed_agents = parse_agents(document.get('agents'))
    cmd_enabled = _parse_cmd_enabled(document)
    layout_spec = _parse_layout_spec(document)
    provider_home_sync = _parse_provider_home_sync(document)
    return _build_project_config(
        default_agents=default_agents,
        parsed_agents=parsed_agents,
        cmd_enabled=cmd_enabled,
        layout_spec=layout_spec,
        provider_home_sync=provider_home_sync,
        source_path=source_path,
    )


def _validate_document_shape(document: dict[str, Any]) -> None:
    unknown_top = sorted(set(document) - ALLOWED_TOP_LEVEL_KEYS)
    if unknown_top:
        raise ConfigValidationError(
            f'config contains unknown top-level fields: {", ".join(unknown_top)}'
        )
    if document.get('version') != 2:
        raise ConfigValidationError('version must be 2')


def _parse_default_agents(document: dict[str, Any]) -> tuple[str, ...]:
    raw_default_agents = document.get('default_agents')
    if raw_default_agents is None:
        raise ConfigValidationError('default_agents is required')
    try:
        return tuple(
            normalize_agent_name(item)
            for item in expect_string_list(raw_default_agents, field_name='default_agents')
        )
    except AgentValidationError as exc:
        raise ConfigValidationError(str(exc)) from exc


def _parse_cmd_enabled(document: dict[str, Any]) -> bool:
    if 'cmd_enabled' not in document:
        return False
    return expect_bool(document['cmd_enabled'], field_name='cmd_enabled')


def _parse_layout_spec(document: dict[str, Any]) -> str | None:
    if document.get('layout') is None:
        return None
    return expect_string(document['layout'], field_name='layout')


def _parse_provider_home_sync(document: dict[str, Any]) -> tuple[str, ...]:
    if document.get('provider_home_sync') is None:
        return ()
    providers = []
    for item in expect_string_list(document['provider_home_sync'], field_name='provider_home_sync'):
        provider = item.strip().lower()
        if provider not in {'codex', 'claude'}:
            raise ConfigValidationError('provider_home_sync only supports: codex, claude')
        if provider not in providers:
            providers.append(provider)
    return tuple(providers)


def _build_project_config(
    *,
    default_agents: tuple[str, ...],
    parsed_agents,
    cmd_enabled: bool,
    layout_spec: str | None,
    provider_home_sync: tuple[str, ...],
    source_path: Path | None,
) -> ProjectConfig:
    try:
        return ProjectConfig(
            version=2,
            default_agents=default_agents,
            agents=parsed_agents,
            cmd_enabled=cmd_enabled,
            layout_spec=layout_spec,
            provider_home_sync=provider_home_sync,
            source_path=str(source_path) if source_path else None,
        )
    except AgentValidationError as exc:
        raise ConfigValidationError(str(exc)) from exc


__all__ = ['validate_project_config']
