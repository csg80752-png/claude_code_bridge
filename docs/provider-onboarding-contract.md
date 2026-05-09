# Provider Onboarding Contract

This document defines the minimum contract for adding a new CCB provider. A provider is not ready to ship until its manifest exposes `ProviderManifest.onboarding_contracts`, focused tests cover that metadata, and live canaries prove the provider behaves through CCB.

The contract is keyed by `RuntimeMode`, not by provider name alone. A provider can support multiple execution shapes with different semantics; for example, a pane-backed Claude session uses tmux paste and observed turn boundaries, while a headless Claude invocation can use structured prompt transport and exact structured completion.

```python
ProviderManifest(
    ...,
    runtime_profiles={RuntimeMode.PANE_BACKED: ...},
    onboarding_contracts={RuntimeMode.PANE_BACKED: ProviderOnboardingContract(...)},
)
```

`onboarding_contracts` must have exactly the same runtime-mode keys as `runtime_profiles`.
The contract must also agree with the runtime profile's completion guarantees: `exact` requires exact-completion support, `observed-boundary` requires observed-completion support, `stability-window` requires reply-stability support, and `terminal-quiet` requires a terminal-text quiet profile/source without exact, observed, or reply-stability support.

The v1 value sets are closed intentionally. Adding a new prompt transport, readiness model, completion model, diagnostic class, restart recovery mode, home isolation ownership, or credential lifecycle requires schema review and either a compatible v1 extension decision or a new `schema_version`.

## Prompt Transport

`prompt_transport` describes how CCB submits a request to the provider.

- `structured`: request is sent through an API or structured IPC path.
- `tmux-paste`: request is pasted into an interactive tmux pane.
- `terminal-text`: request is sent as terminal text without structured completion support.

Evidence required: a focused test or adapter test proving the provider receives the exact CCB request body.

## Readiness

`readiness` describes how CCB decides the provider can safely receive a request.

- `structured`: API/IPC readiness is explicit.
- `pane-safe-consumer`: foreground process and pane state are checked before paste.
- `best-effort`: no strong readiness signal exists; failures must be surfaced diagnostically.

Evidence required: a test for ready, not-ready, and unavailable/dead runtime paths.

## Completion

`completion` describes how CCB decides a job is terminal.

- `exact`: provider emits an exact terminal event or structured result.
- `observed-boundary`: CCB observes a session or turn boundary.
- `stability-window`: CCB waits for a stable reply window.
- `terminal-quiet`: CCB relies on terminal text and quiet-period heuristics.

Evidence required: tests for completed, failed, incomplete, timeout, and interrupted/aborted turns.

## Diagnostics

`diagnostics` describes the expected failure detail.

- `structured`: failures expose typed reasons and provider diagnostics.
- `degraded`: failures expose a reason but may lack full provider detail.
- `minimal`: failures may only expose coarse runtime state.

Evidence required: provider failures must not collapse to unexplained empty replies when a diagnostic reason is available.

## Restart Recovery

`restart_recovery` describes behavior after ccbd or provider restart.

- `resume`: in-flight work can be recovered from provider state.
- `resubmit`: in-flight work must be marked for resubmission.
- `none`: in-flight work cannot be recovered.

Evidence required: restart tests must show the resulting status and reason.

Current runtime recovery policy still has a provider-wide `supports_resume` flag. If a future provider has different restart recovery semantics per runtime mode, the provider manifest can document that split here, but operational recovery logic must be updated before CCB can act on it precisely.

## Home Isolation

`home_isolation` describes ownership of provider runtime home/config state.

- `managed`: CCB creates and owns an isolated provider home.
- `observed`: CCB can inspect or route to an existing home but does not fully own it.
- `external`: the provider owns all relevant home/config state.
- `none`: no provider home isolation is available or applicable.

Evidence required: tests must prove auth/config/cache paths do not unintentionally cross agents.

## Credential Lifecycle

`credential_lifecycle` describes how provider credentials are managed.

- `managed`: CCB provisions or rotates credentials.
- `user`: the user/provider CLI owns login and refresh.
- `external`: a separate external service owns credentials.
- `none`: no credentials are required.

Evidence required: blocked, expired, or missing credentials must surface a reason that operators can act on.

## P0 ABI Freeze Gate

P0 freezes the provider ABI with deterministic test doubles before any new public provider or runtime backend is added. The gate is intentionally credential-free and must not require real Gemini availability.

The deterministic 5+ agent provider set is exported as `P0_DETERMINISTIC_CANARY_AGENT_PROVIDERS` and must contain only names from `TEST_DOUBLE_PROVIDER_NAMES`. The initial set is fake Codex, fake Claude, fake Gemini-shaped, fake legacy terminal-text, and generic fake. `fake-gemini` covers the Gemini-shaped stability-window contract; it is not the real `gemini` provider and must not depend on Gemini login, binaries, network access, or provider credentials.

The P0 gate must cover these ABI fields through manifests, not live provider behavior:

- `prompt_transport`
- `readiness`
- `completion`
- `diagnostics`
- `restart_recovery`
- `home_isolation`
- `credential_lifecycle`

The P0 gate is a contract freeze, not a provider launch. Passing it proves that CCB can validate the provider ABI shape with deterministic fixtures. It does not prove that a real provider is installed, authenticated, or operational.

## Live Canary

Every new provider needs a live canary matrix before merge/install is considered complete.

- `cmd -> new_provider`: exact reply visible through `ccb ask --wait` and cmd reply delivery.
- `agent2 -> new_provider`: only when the request does not self-target the current agent2 session.
- `agent3 -> new_provider`: only when the request does not self-target the current agent3 session.
- restart behavior: either exact resume, explicit resubmit, or explicit nonrecoverable reason.
- failure visibility: OAuth, permission, rate-limit, pane-dead, and timeout failures must produce actionable reasons.

CCB `ccb ask` does not currently expose `--model` or `--effort`. When asking CCB agents to review or test a new provider, encode the intended role and review depth in the prompt and treat the reply as provider-specific evidence rather than a guaranteed effort setting.

## Built-In Provider Contracts

These rows are intentionally explicit so new provider adapters can compare their behavior against the existing provider surface.

| Provider | Runtime mode | prompt_transport | readiness | completion | diagnostics | restart_recovery | home_isolation | credential_lifecycle |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| codex | `pane-backed` | `tmux-paste` | `pane-safe-consumer` | `exact` | `structured` | `resume` | `managed` | `user` |
| claude | `pane-backed` | `tmux-paste` | `pane-safe-consumer` | `observed-boundary` | `structured` | `resume` | `managed` | `user` |
| claude | `headless` | `structured` | `structured` | `exact` | `structured` | `resume` | `managed` | `user` |
| gemini | `pane-backed` | `tmux-paste` | `pane-safe-consumer` | `stability-window` | `degraded` | `resume` | `observed` | `user` |
| gemini | `headless` | `structured` | `structured` | `exact` | `structured` | `resume` | `observed` | `user` |
| opencode | `pane-backed` | `tmux-paste` | `pane-safe-consumer` | `observed-boundary` | `degraded` | `none` | `observed` | `user` |
| droid | `pane-backed` | `terminal-text` | `best-effort` | `terminal-quiet` | `minimal` | `none` | `none` | `external` |

## New Provider Checklist

1. Add runtime profiles for each supported runtime mode.
2. Add a matching `ProviderOnboardingContract` for each supported runtime mode.
3. Add execution adapter and session binding if the provider supports CCB-managed execution.
4. Add runtime launcher if the provider can be started by CCB.
5. Add focused tests for launch, prompt transport, readiness, completion, failure diagnostics, restart recovery, and home isolation.
6. Add doctor/capability evidence if the provider has managed or observed home sync behavior.
7. Run focused tests, full pytest, local subagent review, and live CCB canaries.
