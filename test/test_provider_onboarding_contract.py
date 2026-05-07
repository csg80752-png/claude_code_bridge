from __future__ import annotations

from pathlib import Path

import pytest

from agents.models import RuntimeMode
from provider_core.manifests import ProviderManifest, ProviderOnboardingContract
from provider_core.registry import build_default_backend_registry


EXPECTED_BUILTIN_CONTRACTS = {
    ("codex", RuntimeMode.PANE_BACKED): {
        "prompt_transport": "tmux-paste",
        "readiness": "pane-safe-consumer",
        "completion": "exact",
        "diagnostics": "structured",
        "restart_recovery": "resume",
        "home_isolation": "managed",
        "credential_lifecycle": "user",
    },
    ("claude", RuntimeMode.PANE_BACKED): {
        "prompt_transport": "tmux-paste",
        "readiness": "pane-safe-consumer",
        "completion": "observed-boundary",
        "diagnostics": "structured",
        "restart_recovery": "resume",
        "home_isolation": "managed",
        "credential_lifecycle": "user",
    },
    ("claude", RuntimeMode.HEADLESS): {
        "prompt_transport": "structured",
        "readiness": "structured",
        "completion": "exact",
        "diagnostics": "structured",
        "restart_recovery": "resume",
        "home_isolation": "managed",
        "credential_lifecycle": "user",
    },
    ("gemini", RuntimeMode.PANE_BACKED): {
        "prompt_transport": "tmux-paste",
        "readiness": "pane-safe-consumer",
        "completion": "stability-window",
        "diagnostics": "degraded",
        "restart_recovery": "resume",
        "home_isolation": "observed",
        "credential_lifecycle": "user",
    },
    ("gemini", RuntimeMode.HEADLESS): {
        "prompt_transport": "structured",
        "readiness": "structured",
        "completion": "exact",
        "diagnostics": "structured",
        "restart_recovery": "resume",
        "home_isolation": "observed",
        "credential_lifecycle": "user",
    },
    ("opencode", RuntimeMode.PANE_BACKED): {
        "prompt_transport": "tmux-paste",
        "readiness": "pane-safe-consumer",
        "completion": "observed-boundary",
        "diagnostics": "degraded",
        "restart_recovery": "none",
        "home_isolation": "observed",
        "credential_lifecycle": "user",
    },
    ("droid", RuntimeMode.PANE_BACKED): {
        "prompt_transport": "terminal-text",
        "readiness": "best-effort",
        "completion": "terminal-quiet",
        "diagnostics": "minimal",
        "restart_recovery": "none",
        "home_isolation": "none",
        "credential_lifecycle": "external",
    },
}


def _contract(**overrides: object) -> ProviderOnboardingContract:
    values = {
        "schema_version": 1,
        "prompt_transport": "structured",
        "readiness": "structured",
        "completion": "exact",
        "diagnostics": "structured",
        "restart_recovery": "resume",
        "home_isolation": "none",
        "credential_lifecycle": "none",
        **overrides,
    }
    return ProviderOnboardingContract(**values)


def _runtime_profile(provider: str, runtime_mode: RuntimeMode):
    from completion.models import CompletionFamily, CompletionSourceKind, SelectorFamily
    from completion.profiles import CompletionManifest

    return CompletionManifest(
        provider=provider,
        runtime_mode=runtime_mode.value,
        completion_family=CompletionFamily.STRUCTURED_RESULT,
        completion_source_kind=CompletionSourceKind.STRUCTURED_RESULT_STREAM,
        supports_exact_completion=True,
        supports_observed_completion=False,
        supports_anchor_binding=True,
        supports_reply_stability=False,
        supports_terminal_reason=True,
        selector_family=SelectorFamily.STRUCTURED_RESULT,
    )


def _terminal_quiet_runtime_profile(
    provider: str,
    runtime_mode: RuntimeMode,
    *,
    completion_source_kind=None,
    supports_observed_completion: bool = False,
):
    from completion.models import CompletionFamily, CompletionSourceKind, SelectorFamily
    from completion.profiles import CompletionManifest

    return CompletionManifest(
        provider=provider,
        runtime_mode=runtime_mode.value,
        completion_family=CompletionFamily.TERMINAL_TEXT_QUIET,
        completion_source_kind=completion_source_kind or CompletionSourceKind.TERMINAL_TEXT,
        supports_exact_completion=False,
        supports_observed_completion=supports_observed_completion,
        supports_anchor_binding=False,
        supports_reply_stability=False,
        supports_terminal_reason=False,
        selector_family=SelectorFamily.FINAL_MESSAGE,
    )


def _manifest_with_contracts(
    onboarding_contracts: dict[object, object],
    *,
    provider: str = "fake-contract",
    runtime_mode: RuntimeMode = RuntimeMode.PANE_BACKED,
) -> ProviderManifest:
    return ProviderManifest(
        provider=provider,
        supports_resume=True,
        supports_permission_auto=True,
        supports_stream_watch=True,
        supports_subagents=False,
        supports_workspace_attach=True,
        runtime_profiles={runtime_mode: _runtime_profile(provider, runtime_mode)},
        onboarding_contracts=onboarding_contracts,
    )


def test_builtin_provider_manifests_expose_expected_onboarding_contracts() -> None:
    registry = build_default_backend_registry(include_optional=True, include_test_doubles=False)

    for (provider, runtime_mode), expected in EXPECTED_BUILTIN_CONTRACTS.items():
        backend = registry.get(provider)
        assert backend is not None
        contract = backend.manifest.onboarding_contract_for(runtime_mode)
        assert contract.schema_version == 1
        for field_name, expected_value in expected.items():
            assert getattr(contract, field_name) == expected_value


def test_provider_onboarding_contracts_match_runtime_profiles() -> None:
    registry = build_default_backend_registry(include_optional=True, include_test_doubles=True)

    for provider in registry.manifests():
        assert set(provider.onboarding_contracts) == set(provider.runtime_profiles)


def test_provider_onboarding_contract_document_covers_required_sections() -> None:
    text = Path("docs/provider-onboarding-contract.md").read_text(encoding="utf-8")
    for heading in (
        "Prompt Transport",
        "Readiness",
        "Completion",
        "Diagnostics",
        "Restart Recovery",
        "Home Isolation",
        "Credential Lifecycle",
        "Live Canary",
    ):
        assert f"## {heading}" in text


def test_provider_onboarding_contract_rejects_unknown_values() -> None:
    invalid = {
        "prompt_transport": "paste",
        "readiness": "ready",
        "completion": "done",
        "diagnostics": "logs",
        "restart_recovery": "restart",
        "home_isolation": "synced",
        "credential_lifecycle": "oauth",
    }
    for field_name, bad_value in invalid.items():
        with pytest.raises(ValueError, match=field_name):
            _contract(**{field_name: bad_value})


def test_provider_onboarding_contract_rejects_non_normalized_values() -> None:
    with pytest.raises(ValueError, match="prompt_transport"):
        _contract(prompt_transport=" structured")


def test_provider_onboarding_contract_rejects_unknown_schema_version() -> None:
    with pytest.raises(ValueError, match="schema_version"):
        _contract(schema_version=2)


def test_provider_manifest_rejects_empty_onboarding_contracts() -> None:
    with pytest.raises(ValueError, match="onboarding_contracts cannot be empty"):
        _manifest_with_contracts({})


def test_provider_manifest_rejects_non_runtime_mode_contract_key() -> None:
    with pytest.raises(ValueError, match="onboarding_contracts keys must be RuntimeMode"):
        _manifest_with_contracts({"pane-backed": _contract()})


def test_provider_manifest_rejects_non_contract_value() -> None:
    with pytest.raises(ValueError, match="onboarding_contracts values must be ProviderOnboardingContract"):
        _manifest_with_contracts({RuntimeMode.PANE_BACKED: object()})


def test_provider_manifest_rejects_contract_key_mismatch() -> None:
    with pytest.raises(ValueError, match="onboarding_contracts must match runtime_profiles"):
        _manifest_with_contracts({RuntimeMode.HEADLESS: _contract()})


def test_provider_manifest_rejects_exact_contract_without_exact_profile_support() -> None:
    from completion.models import CompletionFamily, CompletionSourceKind, SelectorFamily
    from completion.profiles import CompletionManifest

    with pytest.raises(ValueError, match="completion exact requires exact profile support"):
        ProviderManifest(
            provider="fake-contract",
            supports_resume=True,
            supports_permission_auto=True,
            supports_stream_watch=True,
            supports_subagents=False,
            supports_workspace_attach=True,
            runtime_profiles={
                RuntimeMode.PANE_BACKED: CompletionManifest(
                    provider="fake-contract",
                    runtime_mode=RuntimeMode.PANE_BACKED.value,
                    completion_family=CompletionFamily.SESSION_BOUNDARY,
                    completion_source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
                    supports_exact_completion=False,
                    supports_observed_completion=True,
                    supports_anchor_binding=True,
                    supports_reply_stability=False,
                    supports_terminal_reason=True,
                    selector_family=SelectorFamily.FINAL_MESSAGE,
                )
            },
            onboarding_contracts={RuntimeMode.PANE_BACKED: _contract(completion="exact")},
        )


def test_provider_manifest_rejects_stability_contract_without_stability_profile_support() -> None:
    with pytest.raises(ValueError, match="completion stability-window requires reply stability support"):
        _manifest_with_contracts({RuntimeMode.PANE_BACKED: _contract(completion="stability-window")})


def test_provider_manifest_rejects_terminal_quiet_contract_without_terminal_text_profile() -> None:
    with pytest.raises(ValueError, match="completion terminal-quiet requires terminal text profile"):
        _manifest_with_contracts({RuntimeMode.PANE_BACKED: _contract(completion="terminal-quiet")})


def test_provider_manifest_rejects_terminal_quiet_contract_without_terminal_text_source() -> None:
    from completion.models import CompletionSourceKind

    with pytest.raises(ValueError, match="completion terminal-quiet requires terminal text source"):
        ProviderManifest(
            provider="fake-contract",
            supports_resume=True,
            supports_permission_auto=True,
            supports_stream_watch=True,
            supports_subagents=False,
            supports_workspace_attach=True,
            runtime_profiles={
                RuntimeMode.PANE_BACKED: _terminal_quiet_runtime_profile(
                    "fake-contract",
                    RuntimeMode.PANE_BACKED,
                    completion_source_kind=CompletionSourceKind.SESSION_SNAPSHOT,
                )
            },
            onboarding_contracts={RuntimeMode.PANE_BACKED: _contract(completion="terminal-quiet")},
        )


def test_provider_manifest_rejects_terminal_quiet_contract_with_observed_support() -> None:
    with pytest.raises(ValueError, match="completion terminal-quiet requires quiet-only profile support"):
        ProviderManifest(
            provider="fake-contract",
            supports_resume=True,
            supports_permission_auto=True,
            supports_stream_watch=True,
            supports_subagents=False,
            supports_workspace_attach=True,
            runtime_profiles={
                RuntimeMode.PANE_BACKED: _terminal_quiet_runtime_profile(
                    "fake-contract",
                    RuntimeMode.PANE_BACKED,
                    supports_observed_completion=True,
                )
            },
            onboarding_contracts={RuntimeMode.PANE_BACKED: _contract(completion="terminal-quiet")},
        )
