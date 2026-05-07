# Provider Onboarding Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a compact provider onboarding contract so future model/provider additions have explicit adapter responsibilities and testable metadata.

**Architecture:** Extend the provider core manifest with a small, stable `ProviderOnboardingContract` dataclass and expose it as `ProviderManifest.onboarding_contracts: dict[RuntimeMode, ProviderOnboardingContract]`. Keep provider-specific completion behavior in existing `CompletionManifest`; the new contract only documents and validates onboarding responsibilities such as launch, prompt transport, readiness, completion, diagnostics, restart recovery, and home/env isolation. The contract is runtime-mode keyed because pane-backed and headless modes can have different prompt transport, readiness, and completion semantics. Add docs that explain how a new provider must satisfy the contract before it can be considered supported.

**Tech Stack:** Python dataclasses, provider_core registry/catalog, pytest, Markdown docs.

---

## Skill Mapping

- `superpowers:systematic-debugging`: use if existing provider metadata conflicts with observed provider behavior.
- `superpowers:test-driven-development`: write RED tests for missing contract metadata before modifying production code.
- `superpowers:requesting-code-review`: after implementation, request local architecture/schema review and, if CCB is healthy, `agent3` operational review.
- `superpowers:verification-before-completion`: focused tests, full pytest, and live canary before merge/install.

## Role/Effort Plan

- Contract authorship: main session.
- Mechanical code additions: main session for the first version; low subagent may only review or add follow-up enumeration tests after the schema is locked.
- Architecture review: local subagent at medium/high effort.
- CCB provider review: `agent3` review prompt; note that CCB does not expose `--effort`, so the prompt asks for architecture-depth review but cannot force model effort.

## Contract Shape

Create `ProviderOnboardingContract` in `lib/provider_core/manifests.py`, then require `ProviderManifest(..., onboarding_contracts={RuntimeMode.X: ProviderOnboardingContract(...)})` with keys matching `runtime_profiles`:

```python
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
```

Stable value sets for v1:

- `prompt_transport`: `structured`, `tmux-paste`, `terminal-text`
- `readiness`: `structured`, `pane-safe-consumer`, `best-effort`
- `completion`: `exact`, `observed-boundary`, `stability-window`, `terminal-quiet`
- `diagnostics`: `structured`, `degraded`, `minimal`
- `restart_recovery`: `resume`, `resubmit`, `none`
- `home_isolation`: `managed`, `observed`, `external`, `none`
- `credential_lifecycle`: `managed`, `user`, `external`, `none`

## Task 1: Add Contract Metadata To Provider Manifests

**Files:**
- Modify: `lib/provider_core/manifests.py`
- Modify: `lib/provider_backends/codex/manifest.py`
- Modify: `lib/provider_backends/claude/manifest.py`
- Modify: `lib/provider_backends/gemini/manifest.py`
- Modify: `lib/provider_backends/opencode/manifest.py`
- Modify: `lib/provider_backends/droid/manifest.py`
- Modify: `lib/provider_core/registry_runtime/test_double_backends.py`
- Test: `test/test_provider_onboarding_contract.py`

- [x] **Step 1: Write RED test for built-in providers**

Create `test/test_provider_onboarding_contract.py` with assertions:

```python
from provider_core.registry import build_default_backend_registry


def test_builtin_provider_manifests_expose_onboarding_contracts() -> None:
    registry = build_default_backend_registry(include_optional=True, include_test_doubles=False)
    expected = {"codex", "claude", "gemini", "opencode", "droid"}

    for provider in expected:
        backend = registry.get(provider)
        assert backend is not None
        contract = backend.manifest.onboarding_contract_for(RuntimeMode.PANE_BACKED)
        assert contract.schema_version == 1
        assert contract.prompt_transport
        assert contract.readiness
        assert contract.completion
        assert contract.diagnostics
        assert contract.restart_recovery
        assert contract.home_isolation
        assert contract.credential_lifecycle
```

- [x] **Step 2: Run RED test**

Run:

```bash
env PYTHONPATH=lib uvx pytest test/test_provider_onboarding_contract.py -q
```

Expected: fail because `ProviderManifest` has no `onboarding_contracts`.

- [x] **Step 3: Add dataclass and validation**

Add `ProviderOnboardingContract` to `lib/provider_core/manifests.py` and require `ProviderManifest(..., onboarding_contracts=...)`.

Validation:
- `schema_version` must equal `1`.
- every string field must be non-empty and in the v1 value set.
- `onboarding_contracts` keys must be `RuntimeMode`.
- `onboarding_contracts` keys must exactly match `runtime_profiles` keys.
- `completion` must agree with the `CompletionManifest` for that runtime mode, including requiring `terminal-quiet` to use a terminal-text quiet profile/source.
- Current runtime recovery policy still uses provider-wide `supports_resume`; document this as an operational limitation for future mixed-mode providers rather than changing dispatcher recovery behavior in PR-G2.

- [x] **Step 4: Populate built-in manifests**

Initial mappings:

| Provider | RuntimeMode | prompt_transport | readiness | completion | diagnostics | restart_recovery | home_isolation | credential_lifecycle |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| codex | `PANE_BACKED` | `tmux-paste` | `pane-safe-consumer` | `exact` | `structured` | `resume` | `managed` | `user` |
| claude | `PANE_BACKED` | `tmux-paste` | `pane-safe-consumer` | `observed-boundary` | `structured` | `resume` | `managed` | `user` |
| claude | `HEADLESS` | `structured` | `structured` | `exact` | `structured` | `resume` | `managed` | `user` |
| gemini | `PANE_BACKED` | `tmux-paste` | `pane-safe-consumer` | `stability-window` | `degraded` | `resume` | `observed` | `user` |
| gemini | `HEADLESS` | `structured` | `structured` | `exact` | `structured` | `resume` | `observed` | `user` |
| opencode | `PANE_BACKED` | `tmux-paste` | `pane-safe-consumer` | `observed-boundary` | `degraded` | `none` | `observed` | `user` |
| droid | `PANE_BACKED` | `terminal-text` | `best-effort` | `terminal-quiet` | `minimal` | `none` | `none` | `external` |

Test doubles can use the closest matching built-in contract shape for their completion family.

- [x] **Step 5: Run focused provider tests**

Run:

```bash
env PYTHONPATH=lib uvx pytest test/test_provider_onboarding_contract.py test/test_v2_provider_catalog.py test/test_v2_provider_core_registry.py -q
```

Expected: pass.

## Task 2: Add Onboarding Documentation

**Files:**
- Create: `docs/provider-onboarding-contract.md`
- Test: `test/test_provider_onboarding_contract.py`

- [x] **Step 1: Add documentation existence/content test**

Extend `test/test_provider_onboarding_contract.py`:

```python
from pathlib import Path


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
```

- [x] **Step 2: Run RED doc test**

Run:

```bash
env PYTHONPATH=lib uvx pytest test/test_provider_onboarding_contract.py::test_provider_onboarding_contract_document_covers_required_sections -q
```

Expected: fail because the doc does not exist.

- [x] **Step 3: Write doc**

Create `docs/provider-onboarding-contract.md` with:
- the v1 value sets.
- expected adapter evidence for each field.
- new provider checklist.
- live canary matrix:
  - `cmd -> new_provider`
  - `agent2 -> new_provider` if not self-target
  - restart/resume or resubmit behavior
  - failure reason visibility

- [x] **Step 4: Run doc test**

Run:

```bash
env PYTHONPATH=lib uvx pytest test/test_provider_onboarding_contract.py -q
```

Expected: pass.

## Task 3: Review, Verify, PR

- [x] **Step 1: Focused tests**

Run:

```bash
env PYTHONPATH=lib uvx pytest test/test_provider_onboarding_contract.py test/test_v2_provider_catalog.py test/test_v2_provider_core_registry.py test/test_provider_home_sync.py -q
```

- [ ] **Step 2: Full pytest**

Run:

```bash
env PYTHONPATH=lib uvx pytest -q
```

- [x] **Step 3: Reviews**

Request:
- local architecture/schema review for contract value sets and manifest lock-in risk.
- local code review for diff/test correctness.
- CCB `agent3` review with architecture-depth prompt. Record that CCB cannot force `--effort`.

- [ ] **Step 4: Live canary after merge/install**

Run:

```bash
ccb ask --wait --timeout 90 agent2 -- 'provider onboarding contract post-merge canary agent2. Reply exactly: pong-provider-contract-agent2'
ccb ask --wait --timeout 180 agent3 -- 'provider onboarding contract post-merge canary agent3. Reply exactly: pong-provider-contract-agent3'
```

Expected: exact replies and daemon install match true.
