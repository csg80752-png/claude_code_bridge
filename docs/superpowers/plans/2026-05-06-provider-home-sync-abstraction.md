# Provider Home Sync Abstraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract provider-agnostic project home sync orchestration while preserving the existing hardened Codex sync behavior.

**Architecture:** Keep provider-specific filesystem policy in provider adapters, and move shared agent discovery, profile-home skipping, managed-home validation, symlink ancestor checks, summary dataclasses, and service orchestration into `cli.services.provider_home_sync`. The existing `sync-codex-home` command remains the public CLI and delegates through a Codex policy adapter.

**Tech Stack:** Python stdlib dataclasses/pathlib, existing CCB phase2 CLI services, pytest.

---

### Task 1: Generic Provider Home Sync Service

**Files:**
- Create: `lib/cli/services/provider_home_sync.py`
- Test: `test/test_provider_home_sync.py`

- [ ] **Step 1: Write failing tests**

Add tests with a fake provider policy proving the generic service:
- syncs only configured agents for the requested provider
- skips missing/unmanaged homes without creating them
- skips profile-backed homes with `reason="profile-home"`
- skips symlinked runtime ancestors with `reason="unmanaged-home"`
- returns stable synced/skipped summary dataclasses

- [ ] **Step 2: Run RED**

Run:

```bash
PYTHONPATH=lib /home/speed/.local/bin/uv run --with pytest python -m pytest test/test_provider_home_sync.py -q
```

Expected: fail because `cli.services.provider_home_sync` does not exist.

- [ ] **Step 3: Implement generic service**

Create `ProviderHomeSyncPolicy`, `ProviderHomeSyncAgentResult`, `ProviderHomeSyncSkippedAgent`, `ProviderHomeSyncSummary`, and `sync_project_provider_homes(...)`. Keep all copy behavior inside the policy callback; the generic service only discovers agents and validates managed homes.

- [ ] **Step 4: Run GREEN**

Run the same targeted test and expect pass.

### Task 2: Move Codex Service Onto Generic Orchestration

**Files:**
- Modify: `lib/cli/services/codex_home_sync.py`
- Test: `test/test_codex_home_sync.py`

- [ ] **Step 1: Write/adjust failing compatibility tests**

Keep existing Codex tests intact. They should continue importing `CodexHomeSyncAgentResult`, `CodexHomeSyncSkippedAgent`, and `CodexHomeSyncSummary` from `cli.services.codex_home_sync`.

- [ ] **Step 2: Implement Codex policy adapter**

In `codex_home_sync.py`, define a `ProviderHomeSyncPolicy` for Codex:
- provider: `codex`
- source home: `system_codex_home`
- runtime home: `isolated_home_for_runtime`
- profile home: current Codex provider profile resolver
- managed sentinel: `_POLICY_FILENAME`
- sync callback: `sync_codex_home_from_source`

- [ ] **Step 3: Run Codex regression tests**

Run:

```bash
PYTHONPATH=lib /home/speed/.local/bin/uv run --with pytest python -m pytest test/test_codex_home_sync.py -q
```

Expected: pass.

### Task 3: Verification and Review

**Files:**
- All touched files

- [ ] **Step 1: Run focused suite**

Run:

```bash
PYTHONPATH=lib /home/speed/.local/bin/uv run --with pytest python -m pytest \
  test/test_provider_home_sync.py \
  test/test_codex_home_sync.py \
  test/test_v2_cli_parser.py \
  test/test_v2_runtime_launch.py::test_codex_namespace_isolation_per_binding_does_not_leak_global_codex_home \
  test/test_b5_canary_suite.py::test_b5_codex_home_isolation_two_bindings_no_state_collision -q
```

- [ ] **Step 2: Request focused review**

Ask one subagent to review for abstraction boundaries, behavior preservation, and whether PR-F2/F3 can build on the policy interface without copying Codex-specific assumptions.

- [ ] **Step 3: Commit/PR**

Commit only the abstraction delta and open a stacked PR on top of `v8.5-pr-sync-codex-home-hardening` unless PR #18 has already merged.
