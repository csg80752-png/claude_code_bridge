from __future__ import annotations

from dataclasses import dataclass

from agents.models import RuntimeMode
from completion.models import CompletionFamily, CompletionSourceKind
from completion.profiles import CompletionManifest

_PROMPT_TRANSPORT_VALUES = frozenset({'structured', 'tmux-paste', 'terminal-text'})
_READINESS_VALUES = frozenset({'structured', 'pane-safe-consumer', 'best-effort'})
_COMPLETION_VALUES = frozenset({'exact', 'observed-boundary', 'stability-window', 'terminal-quiet'})
_DIAGNOSTICS_VALUES = frozenset({'structured', 'degraded', 'minimal'})
_RESTART_RECOVERY_VALUES = frozenset({'resume', 'resubmit', 'none'})
_HOME_ISOLATION_VALUES = frozenset({'managed', 'observed', 'external', 'none'})
_CREDENTIAL_LIFECYCLE_VALUES = frozenset({'managed', 'user', 'external', 'none'})


@dataclass(frozen=True)
class ProviderOnboardingContract:
    schema_version: int
    prompt_transport: str
    readiness: str
    completion: str
    diagnostics: str
    restart_recovery: str
    home_isolation: str
    credential_lifecycle: str

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError('provider onboarding contract schema_version must be 1')
        _validate_contract_value('prompt_transport', self.prompt_transport, _PROMPT_TRANSPORT_VALUES)
        _validate_contract_value('readiness', self.readiness, _READINESS_VALUES)
        _validate_contract_value('completion', self.completion, _COMPLETION_VALUES)
        _validate_contract_value('diagnostics', self.diagnostics, _DIAGNOSTICS_VALUES)
        _validate_contract_value('restart_recovery', self.restart_recovery, _RESTART_RECOVERY_VALUES)
        _validate_contract_value('home_isolation', self.home_isolation, _HOME_ISOLATION_VALUES)
        _validate_contract_value('credential_lifecycle', self.credential_lifecycle, _CREDENTIAL_LIFECYCLE_VALUES)


@dataclass(frozen=True)
class ProviderManifest:
    provider: str
    supports_resume: bool
    supports_permission_auto: bool
    supports_stream_watch: bool
    supports_subagents: bool
    supports_workspace_attach: bool
    runtime_profiles: dict[RuntimeMode, CompletionManifest]
    onboarding_contracts: dict[RuntimeMode, ProviderOnboardingContract]

    def __post_init__(self) -> None:
        provider = (self.provider or '').strip().lower()
        if not provider:
            raise ValueError('provider cannot be empty')
        object.__setattr__(self, 'provider', provider)
        if not self.runtime_profiles:
            raise ValueError('runtime_profiles cannot be empty')
        if not self.onboarding_contracts:
            raise ValueError('onboarding_contracts cannot be empty')
        normalized: dict[RuntimeMode, CompletionManifest] = {}
        for runtime_mode, profile in dict(self.runtime_profiles).items():
            if profile.provider != provider:
                raise ValueError(
                    f'runtime profile provider {profile.provider!r} does not match manifest provider {provider!r}'
                )
            if profile.runtime_mode != runtime_mode.value:
                raise ValueError(
                    f'runtime profile mode {profile.runtime_mode!r} does not match runtime key {runtime_mode.value!r}'
                )
            normalized[runtime_mode] = profile
        object.__setattr__(self, 'runtime_profiles', normalized)
        contracts: dict[RuntimeMode, ProviderOnboardingContract] = {}
        for runtime_mode, contract in dict(self.onboarding_contracts).items():
            if not isinstance(runtime_mode, RuntimeMode):
                raise ValueError('onboarding_contracts keys must be RuntimeMode')
            if not isinstance(contract, ProviderOnboardingContract):
                raise ValueError('onboarding_contracts values must be ProviderOnboardingContract')
            contracts[runtime_mode] = contract
        if set(contracts) != set(normalized):
            raise ValueError('onboarding_contracts must match runtime_profiles')
        for runtime_mode, contract in contracts.items():
            _validate_contract_matches_profile(runtime_mode, contract, normalized[runtime_mode])
        object.__setattr__(self, 'onboarding_contracts', contracts)

    def supports_runtime_mode(self, runtime_mode: RuntimeMode) -> bool:
        return runtime_mode in self.runtime_profiles

    def completion_manifest_for(self, runtime_mode: RuntimeMode) -> CompletionManifest:
        return self.runtime_profiles[runtime_mode]

    def onboarding_contract_for(self, runtime_mode: RuntimeMode) -> ProviderOnboardingContract:
        return self.onboarding_contracts[runtime_mode]


def _validate_contract_value(field_name: str, value: str, allowed: frozenset[str]) -> None:
    normalized = str(value or '').strip()
    if normalized != value:
        raise ValueError(f'{field_name} must be normalized')
    if normalized not in allowed:
        choices = ', '.join(sorted(allowed))
        raise ValueError(f'{field_name} must be one of: {choices}')


def _validate_contract_matches_profile(
    runtime_mode: RuntimeMode,
    contract: ProviderOnboardingContract,
    profile: CompletionManifest,
) -> None:
    if contract.completion == 'exact' and not profile.supports_exact_completion:
        raise ValueError(f'onboarding_contracts[{runtime_mode.value}].completion exact requires exact profile support')
    if contract.completion == 'observed-boundary' and not profile.supports_observed_completion:
        raise ValueError(
            f'onboarding_contracts[{runtime_mode.value}].completion observed-boundary requires observed profile support'
        )
    if contract.completion == 'stability-window' and not profile.supports_reply_stability:
        raise ValueError(
            f'onboarding_contracts[{runtime_mode.value}].completion stability-window requires reply stability support'
        )
    if contract.completion == 'terminal-quiet':
        if profile.completion_family is not CompletionFamily.TERMINAL_TEXT_QUIET:
            raise ValueError(
                f'onboarding_contracts[{runtime_mode.value}].completion terminal-quiet requires terminal text profile'
            )
        if profile.completion_source_kind is not CompletionSourceKind.TERMINAL_TEXT:
            raise ValueError(
                f'onboarding_contracts[{runtime_mode.value}].completion terminal-quiet requires terminal text source'
            )
        if profile.supports_exact_completion or profile.supports_observed_completion or profile.supports_reply_stability:
            raise ValueError(
                f'onboarding_contracts[{runtime_mode.value}].completion terminal-quiet requires quiet-only profile support'
            )


__all__ = ['ProviderManifest', 'ProviderOnboardingContract']
