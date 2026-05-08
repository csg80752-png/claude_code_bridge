from __future__ import annotations

from agents.models import RuntimeMode
from completion.models import CompletionFamily, CompletionSourceKind, SelectorFamily
from completion.profiles import CompletionManifest
from provider_execution.fake import FakeProviderAdapter

from provider_core.contracts import ProviderBackend
from provider_core.manifests import ProviderManifest, ProviderOnboardingContract

TEST_DOUBLE_PROVIDER_NAMES = ("fake", "fake-codex", "fake-claude", "fake-gemini", "fake-legacy")
# Ordered by ABI-shape priority for the P0 deterministic gate, not by registry build order.
# The generic fake remains load-bearing for structured/headless-style defaults and no-credential coverage.
P0_DETERMINISTIC_CANARY_AGENT_PROVIDERS = (
    "fake-codex",
    "fake-claude",
    "fake-gemini",
    "fake-legacy",
    "fake",
)


def _contract(
    *,
    prompt_transport: str = "structured",
    readiness: str = "structured",
    completion: str = "exact",
    diagnostics: str = "structured",
    restart_recovery: str = "resume",
    home_isolation: str = "none",
    credential_lifecycle: str = "none",
) -> ProviderOnboardingContract:
    return ProviderOnboardingContract(
        schema_version=1,
        prompt_transport=prompt_transport,
        readiness=readiness,
        completion=completion,
        diagnostics=diagnostics,
        restart_recovery=restart_recovery,
        home_isolation=home_isolation,
        credential_lifecycle=credential_lifecycle,
    )


def build_test_double_backends() -> list[ProviderBackend]:
    return [
        ProviderBackend(
            manifest=ProviderManifest(
                provider="fake",
                supports_resume=True,
                supports_permission_auto=True,
                supports_stream_watch=True,
                supports_subagents=False,
                supports_workspace_attach=True,
                onboarding_contracts={
                    RuntimeMode.PANE_BACKED: _contract(),
                    RuntimeMode.HEADLESS: _contract(),
                },
                runtime_profiles={
                    RuntimeMode.PANE_BACKED: CompletionManifest(
                        provider="fake",
                        runtime_mode=RuntimeMode.PANE_BACKED.value,
                        completion_family=CompletionFamily.STRUCTURED_RESULT,
                        completion_source_kind=CompletionSourceKind.STRUCTURED_RESULT_STREAM,
                        supports_exact_completion=True,
                        supports_observed_completion=False,
                        supports_anchor_binding=True,
                        supports_reply_stability=False,
                        supports_terminal_reason=True,
                        selector_family=SelectorFamily.STRUCTURED_RESULT,
                    ),
                    RuntimeMode.HEADLESS: CompletionManifest(
                        provider="fake",
                        runtime_mode=RuntimeMode.HEADLESS.value,
                        completion_family=CompletionFamily.STRUCTURED_RESULT,
                        completion_source_kind=CompletionSourceKind.STRUCTURED_RESULT_STREAM,
                        supports_exact_completion=True,
                        supports_observed_completion=False,
                        supports_anchor_binding=True,
                        supports_reply_stability=False,
                        supports_terminal_reason=True,
                        selector_family=SelectorFamily.STRUCTURED_RESULT,
                    ),
                },
            ),
            execution_adapter=FakeProviderAdapter(),
        ),
        ProviderBackend(
            manifest=ProviderManifest(
                provider="fake-codex",
                supports_resume=True,
                supports_permission_auto=True,
                supports_stream_watch=True,
                supports_subagents=False,
                supports_workspace_attach=True,
                onboarding_contracts={
                    RuntimeMode.PANE_BACKED: _contract(
                        prompt_transport="tmux-paste",
                        readiness="pane-safe-consumer",
                        completion="exact",
                        home_isolation="managed",
                        credential_lifecycle="user",
                    ),
                },
                runtime_profiles={
                    RuntimeMode.PANE_BACKED: CompletionManifest(
                        provider="fake-codex",
                        runtime_mode=RuntimeMode.PANE_BACKED.value,
                        completion_family=CompletionFamily.PROTOCOL_TURN,
                        completion_source_kind=CompletionSourceKind.PROTOCOL_EVENT_STREAM,
                        supports_exact_completion=True,
                        supports_observed_completion=False,
                        supports_anchor_binding=True,
                        supports_reply_stability=False,
                        supports_terminal_reason=True,
                        selector_family=SelectorFamily.FINAL_MESSAGE,
                    ),
                },
            ),
            execution_adapter=FakeProviderAdapter(
                provider="fake-codex",
                source_kind=CompletionSourceKind.PROTOCOL_EVENT_STREAM,
                script_mode="protocol_turn",
            ),
        ),
        ProviderBackend(
            manifest=ProviderManifest(
                provider="fake-claude",
                supports_resume=True,
                supports_permission_auto=True,
                supports_stream_watch=True,
                supports_subagents=False,
                supports_workspace_attach=True,
                onboarding_contracts={
                    RuntimeMode.PANE_BACKED: _contract(
                        prompt_transport="tmux-paste",
                        readiness="pane-safe-consumer",
                        completion="observed-boundary",
                        credential_lifecycle="user",
                    ),
                },
                runtime_profiles={
                    RuntimeMode.PANE_BACKED: CompletionManifest(
                        provider="fake-claude",
                        runtime_mode=RuntimeMode.PANE_BACKED.value,
                        completion_family=CompletionFamily.SESSION_BOUNDARY,
                        completion_source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
                        supports_exact_completion=False,
                        supports_observed_completion=True,
                        supports_anchor_binding=True,
                        supports_reply_stability=False,
                        supports_terminal_reason=True,
                        selector_family=SelectorFamily.FINAL_MESSAGE,
                    ),
                },
            ),
            execution_adapter=FakeProviderAdapter(
                provider="fake-claude",
                source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
                script_mode="session_boundary",
            ),
        ),
        ProviderBackend(
            manifest=ProviderManifest(
                provider="fake-gemini",
                supports_resume=True,
                supports_permission_auto=True,
                supports_stream_watch=True,
                supports_subagents=False,
                supports_workspace_attach=True,
                onboarding_contracts={
                    RuntimeMode.PANE_BACKED: _contract(
                        prompt_transport="tmux-paste",
                        readiness="pane-safe-consumer",
                        completion="stability-window",
                        diagnostics="degraded",
                        credential_lifecycle="user",
                    ),
                },
                runtime_profiles={
                    RuntimeMode.PANE_BACKED: CompletionManifest(
                        provider="fake-gemini",
                        runtime_mode=RuntimeMode.PANE_BACKED.value,
                        completion_family=CompletionFamily.ANCHORED_SESSION_STABILITY,
                        completion_source_kind=CompletionSourceKind.SESSION_SNAPSHOT,
                        supports_exact_completion=False,
                        supports_observed_completion=True,
                        supports_anchor_binding=True,
                        supports_reply_stability=True,
                        supports_terminal_reason=True,
                        selector_family=SelectorFamily.SESSION_REPLY,
                    ),
                },
            ),
            execution_adapter=FakeProviderAdapter(
                provider="fake-gemini",
                source_kind=CompletionSourceKind.SESSION_SNAPSHOT,
                script_mode="anchored_session_stability",
            ),
        ),
        ProviderBackend(
            manifest=ProviderManifest(
                provider="fake-legacy",
                supports_resume=True,
                supports_permission_auto=True,
                supports_stream_watch=True,
                supports_subagents=False,
                supports_workspace_attach=True,
                onboarding_contracts={
                    RuntimeMode.PANE_BACKED: _contract(
                        prompt_transport="terminal-text",
                        readiness="best-effort",
                        completion="terminal-quiet",
                        diagnostics="minimal",
                        restart_recovery="none",
                    ),
                },
                runtime_profiles={
                    RuntimeMode.PANE_BACKED: CompletionManifest(
                        provider="fake-legacy",
                        runtime_mode=RuntimeMode.PANE_BACKED.value,
                        completion_family=CompletionFamily.TERMINAL_TEXT_QUIET,
                        completion_source_kind=CompletionSourceKind.TERMINAL_TEXT,
                        supports_exact_completion=False,
                        supports_observed_completion=False,
                        supports_anchor_binding=False,
                        supports_reply_stability=False,
                        supports_terminal_reason=False,
                        selector_family=SelectorFamily.FINAL_MESSAGE,
                    ),
                },
            ),
            execution_adapter=FakeProviderAdapter(
                provider="fake-legacy",
                source_kind=CompletionSourceKind.TERMINAL_TEXT,
                script_mode="legacy_text",
            ),
        ),
    ]


__all__ = [
    "P0_DETERMINISTIC_CANARY_AGENT_PROVIDERS",
    "TEST_DOUBLE_PROVIDER_NAMES",
    "build_test_double_backends",
]
