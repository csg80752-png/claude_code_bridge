# PR 34 Provider Capability Rework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework PR #34 so provider home sync capability diagnostics avoid schema lock-in and match the accepted ownership/status model.

**Architecture:** Keep config schema unchanged. Move capability metadata into the provider home sync policy model with `schema_version`, enum-like `status`, and `ownership` fields. Expose provider policies through public accessors and a registry lookup instead of doctor importing private provider functions or branching on provider names.

**Tech Stack:** Python dataclasses, existing CCB doctor renderer, pytest.

---

### Task 1: Capability Model and Registry

**Files:**
- Modify: `lib/cli/services/provider_home_sync.py`
- Modify: `lib/cli/services/claude_home_sync.py`
- Modify: `lib/cli/services/codex_home_sync.py`
- Modify: `lib/cli/services/doctor_runtime/provider_home.py`
- Test: `test/test_provider_home_sync.py`
- Test: `test/test_v2_tmux_cleanup_history.py`

- [x] **Step 1: Add RED tests**

Assert capability records include `schema_version` and `ownership`, do not include `mode`, do not include sibling `ownership_model`, and are emitted for disabled providers.

- [x] **Step 2: Run RED tests**

Run: `env PYTHONPATH=lib uvx pytest test/test_provider_home_sync.py test/test_v2_tmux_cleanup_history.py::test_doctor_summary_includes_provider_home_sync_status -q`
Expected: fail on current PR #34 shape.

- [x] **Step 3: Implement model and registry**

Replace `mode` with `ownership`, add `schema_version=1`, publish `claude_home_sync_policy()` / `codex_home_sync_policy()`, and use a registry map in doctor runtime.

- [x] **Step 4: Run targeted tests**

Run: `env PYTHONPATH=lib uvx pytest test/test_provider_home_sync.py test/test_claude_home_sync.py test/test_codex_home_sync.py test/test_v2_tmux_cleanup_history.py test/test_v2_cli_render.py -q`
Expected: pass.

### Task 2: Doctor Output Noise Control

**Files:**
- Modify: `lib/cli/render_runtime/ops_views_doctor.py`
- Test: `test/test_v2_cli_render.py`

- [x] **Step 1: Add RED render test**

Assert default doctor output contains a provider home sync capability summary line but not per-capability prose rows.

- [x] **Step 2: Implement summary renderer**

Render compact `provider_home_sync_capabilities: provider=... count=... names=...` line.

- [x] **Step 3: Run render tests**

Run: `env PYTHONPATH=lib uvx pytest test/test_v2_cli_render.py -q`
Expected: pass.

### Task 3: Verification and Review

- [x] **Step 1: Run full focused suite**

Run: `env PYTHONPATH=lib uvx pytest test/test_provider_home_sync.py test/test_claude_home_sync.py test/test_codex_home_sync.py test/test_v2_tmux_cleanup_history.py test/test_v2_cli_render.py -q`

- [x] **Step 2: Run full pytest**

Run: `env PYTHONPATH=lib uvx pytest -q`

- [x] **Step 3: Request adversarial and operational reviews before force-push/merge**

No merge/install before two review axes pass.

Completed verification:
- RED: `env PYTHONPATH=lib uvx pytest test/test_v2_tmux_cleanup_history.py::test_doctor_summary_includes_provider_home_sync_status test/test_v2_cli_render.py::test_render_ps_and_doctor_keep_expected_line_shapes -q` failed on the inherited PR #34 shape.
- Focused before registry refactor: `env PYTHONPATH=lib uvx pytest test/test_provider_home_sync.py test/test_claude_home_sync.py test/test_codex_home_sync.py test/test_v2_tmux_cleanup_history.py test/test_v2_cli_render.py -q` passed with `60 passed`.
- Registry RED: `env PYTHONPATH=lib uvx pytest test/test_provider_home_sync.py::test_provider_home_sync_policy_registry_exposes_supported_public_policies -q` failed with `ModuleNotFoundError` before adding the public registry.
- Focused after registry refactor: `env PYTHONPATH=lib uvx pytest test/test_provider_home_sync.py test/test_claude_home_sync.py test/test_codex_home_sync.py test/test_v2_tmux_cleanup_history.py test/test_v2_cli_render.py -q` passed with `61 passed`.
- Full first run: `env PYTHONPATH=lib uvx pytest -q` had one restart-recovery flake in `test_ccb_two_named_codex_agents_recover_after_ccbd_restart`; the failing test passed on immediate single-test rerun.
- Full final run: `env PYTHONPATH=lib uvx pytest -q` passed with `1866 passed, 17 skipped`.
- Diff hygiene: `git diff --check` passed.
- Reviews: adversarial/schema and operational/doctor UX reviews both passed after the registry file was included in the PR scope.
