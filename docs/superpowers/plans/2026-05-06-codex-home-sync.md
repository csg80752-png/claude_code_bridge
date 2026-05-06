# Codex Home Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a safe CCB command to refresh inherited Codex configuration assets from the operator Codex home into existing agent-scoped `codex-home` directories without copying runtime state.

**Architecture:** Reuse the existing Codex namespace isolation helpers and add a focused sync service that targets only known safe entries. Wire the service through the existing phase2 CLI parser/dispatcher so `ccb sync-codex-home` can be run from a project root.

**Tech Stack:** Python stdlib, existing CCB phase2 CLI parser/dispatcher, pytest/unittest test suite.

---

### Task 1: Safe Sync Primitive

**Files:**
- Modify: `lib/provider_backends/codex/launcher_runtime/codex_namespace_isolation.py`
- Test: `test/test_codex_home_sync.py`

- [ ] **Step 1: Write failing tests**

Add tests that create a fake system `CODEX_HOME` and two runtime dirs. Assert default sync copies `config.toml`, `skills`, `commands`, and `rules`, does not copy `auth.json` unless requested, and never copies `sessions`, `history.jsonl`, `log`, `logs_2.sqlite`, `state_5.sqlite`, `shell_snapshots`, `tmp`, `.tmp`, `cache`, or `plugins`.

- [ ] **Step 2: Run RED**

Run:

```bash
PYTHONPATH=lib python3 -m pytest test/test_codex_home_sync.py -q
```

Expected: fail because the sync API does not exist.

- [ ] **Step 3: Implement primitive**

Add `sync_codex_home_from_source(isolated_home, source_home, include_auth=False) -> CodexHomeSyncResult` and supporting dataclasses/constants. Use existing `_refresh_inherited_entry()` so stale entries are replaced atomically enough for current patterns. Keep denylisted runtime state out of scope.

- [ ] **Step 4: Run GREEN**

Run:

```bash
PYTHONPATH=lib python3 -m pytest test/test_codex_home_sync.py -q
```

Expected: pass.

### Task 2: Project Agent Discovery and Service

**Files:**
- Create: `lib/cli/services/codex_home_sync.py`
- Test: `test/test_codex_home_sync.py`

- [ ] **Step 1: Write failing service tests**

Add tests that build `.ccb/agents/agent1/provider-runtime/codex/codex-home` and `.ccb/agents/agent2/provider-runtime/claude`, then assert only Codex homes are synced. Assert missing `.ccb/agents` returns an empty result without creating unrelated paths.

- [ ] **Step 2: Run RED**

Run:

```bash
PYTHONPATH=lib python3 -m pytest test/test_codex_home_sync.py -q
```

Expected: fail because service module does not exist.

- [ ] **Step 3: Implement service**

Add `sync_project_codex_homes(context, command=None, source_home=None)` that discovers configured Codex agents, targets their managed isolated `codex-home` directories, and calls the primitive. Skip any existing `codex-home` without the isolation policy sentinel to avoid overwriting explicit user-managed homes.

- [ ] **Step 4: Run GREEN**

Run same targeted test and expect pass.

### Task 3: CLI Command

**Files:**
- Modify: `lib/cli/models_start.py`
- Modify: `lib/cli/models.py`
- Modify: `lib/cli/parser_runtime/constants.py`
- Modify: `lib/cli/parser_runtime/commands.py`
- Modify: `lib/cli/parser.py`
- Modify: `lib/cli/phase2.py`
- Modify: `lib/cli/phase2_runtime/dispatch.py`
- Modify: `lib/cli/phase2_runtime/handlers_ops.py`
- Test: `test/test_codex_home_sync.py`

- [ ] **Step 1: Write failing parser/handler tests**

Add tests for `CliParser().parse(["sync-codex-home"])`, `CliParser().parse(["sync-codex-home", "--include-auth"])`, and invalid extra args. Add a command integration test using `maybe_handle_phase2` or the handler directly to assert output includes synced agent names and paths.

- [ ] **Step 2: Run RED**

Run:

```bash
PYTHONPATH=lib python3 -m pytest test/test_codex_home_sync.py -q
```

Expected: fail because parser does not know `sync-codex-home`.

- [ ] **Step 3: Implement parser and handler**

Add `ParsedSyncCodexHomeCommand` with `include_auth: bool`. Add subcommand `sync-codex-home`; route it to a handler that calls `sync_project_codex_homes` and prints stable `command_status: synced` output plus per-agent summary.

- [ ] **Step 4: Run GREEN and related parser tests**

Run:

```bash
PYTHONPATH=lib python3 -m pytest test/test_codex_home_sync.py test/test_v2_config_loader.py -q
```

Expected: pass.

### Task 4: Review and Verification

**Files:**
- All touched files

- [ ] **Step 1: Run focused suite**

Run:

```bash
PYTHONPATH=lib python3 -m pytest test/test_codex_home_sync.py test/test_v2_runtime_launch.py::test_codex_namespace_isolation_per_binding_does_not_leak_global_codex_home test/test_b5_canary_suite.py::test_b5_codex_home_isolation_two_bindings_no_state_collision -q
```

- [ ] **Step 2: Run code review**

Request subagent review for spec compliance and code quality. Fix critical or important findings.

- [ ] **Step 3: Final status**

Report changed files, verification output, and any remaining caveats.

## Self-Review

- Spec coverage: The plan covers safe entry sync, auth opt-in, runtime-state denylist, project-agent discovery, CLI parser, handler output, and verification.
- Placeholder scan: No TBD/TODO placeholders.
- Type consistency: The command name is consistently `sync-codex-home`; auth flag is consistently `include_auth`.
