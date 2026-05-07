# Operational Hardening Roadmap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the current v54 follow-up work without mixing unrelated fixes, keeping restart/runtime safety and provider home inheritance separately reviewable.

**Architecture:** Split the remaining work into independently testable PRs. Runtime supervision hardening stays under `lib/ccbd/supervision/*`; Codex home inheritance stays under provider profile/home sync code. Each PR gets focused tests, full pytest, local adversarial review, and live agent2/agent3 canaries after install.

**Tech Stack:** Python dataclasses/services, pytest, CCB live canaries, GitHub PR flow.

---

## Skill And Model Role Declaration

- Coordinator/integrator: current agent1 session, medium reasoning, owns final scope decisions and merge/install gates.
- Architecture/schema reviewer: local subagent, medium reasoning, read-only adversarial review.
- Mechanical reviewer: local subagent, low reasoning, verifies call sites/tests/docs and catches stale expectations.
- CCB operational reviewer: agent3 when healthy; prompt requests architecture-depth review because CCB does not expose `--effort`.
- `superpowers:systematic-debugging`: required before changing runtime/supervision behavior.
- `superpowers:test-driven-development`: required for any new behavior or regression fix.
- `superpowers:requesting-code-review`: required after implementation and before merge/install.
- `superpowers:verification-before-completion`: required before claiming completion, PR creation, merge, or install.

## Current State Snapshot

- fork/v6 HEAD in this worktree: `4d52857 Add provider onboarding contracts (#54)`.
- Live statusline install: `codex-dual.v54`, generation `213`, healthy, agent1/2/3 idle.
- Local dirty changes: 10 modified files plus `runtime_binding.py` and this plan, covering supervision unbound-runtime failure and Codex home inheritance from non-CCB source homes.
- statusline local layout override: `(cmd; agent1:codex), (agent2:codex; agent3:claude)`.

## PR-H1: Fail Mounts That Produce Pane-Backed Unbound Runtimes

**Files:**
- Modify: `lib/ccbd/supervision/loop.py`
- Modify: `lib/ccbd/supervision/loop_actions.py`
- Modify: `lib/ccbd/supervision/loop_runtime.py`
- Add: `lib/ccbd/supervision/runtime_binding.py`
- Modify: `lib/ccbd/supervision/mount_runtime/service.py`
- Modify: `lib/ccbd/supervision/mount_runtime/transitions.py`
- Test: `test/test_v2_ccbd_supervision_loop.py`

- [x] **Step 1: Verify focused supervision tests**

Run:

```bash
env PYTHONPATH=lib uvx pytest test/test_v2_ccbd_supervision_loop.py -q
```

Expected: pass. If it fails, inspect the failure before editing.

- [x] **Step 2: Review behavior boundaries**

Confirm pane-backed runtimes that mount to `STARTING/healthy` without `runtime_ref`, `session_ref`, `pane_id`, or `active_pane_id` become `FAILED` with `last_failure_reason == "mount-produced-unbound-runtime"`.

Confirm `RuntimeMode.HEADLESS` and `RuntimeMode.PTY_BACKED` do not require pane bindings.

- [x] **Step 3: Request adversarial review**

Ask a medium subagent to review:

```text
Review PR-H1 supervision hardening. Does it correctly fail pane-backed mounts that produce unbound runtimes without breaking headless/pty runtime modes? Check duplicate helper logic in loop_runtime/transitions and whether pane-missing reflow behavior changed safely.
```

Expected: no blockers; important findings must be fixed or explicitly deferred.

Review outcome: no blockers. Fixed helper duplication by moving binding predicates into `runtime_binding.py`, renamed the stale STARTING test to match behavior, and added direct PTY coverage.

## PR-H2: Prevent CCB-Isolated CODEX_HOME From Becoming The Source Home

**Files:**
- Modify: `lib/provider_backends/codex/launcher_runtime/codex_namespace_isolation.py`
- Modify: `lib/provider_profiles/materializer.py`
- Test: `test/test_codex_home_sync.py`
- Test: `test/test_provider_profiles.py`

- [x] **Step 1: Verify focused Codex home tests**

Run:

```bash
env PYTHONPATH=lib uvx pytest test/test_codex_home_sync.py test/test_provider_profiles.py -q
```

Expected: pass.

- [x] **Step 2: Review behavior boundaries**

Confirm `system_codex_home()` honors explicit non-CCB `CODEX_HOME`, but ignores CCB isolated homes shaped like `.ccb/agents/<agent>/provider-runtime/codex/codex-home` and falls back to `$HOME/.codex`.

Confirm provider profile materialization inherits auth/config from the operator home, not from another agent's isolated home.

- [x] **Step 3: Request adversarial review**

Ask a medium subagent to review:

```text
Review PR-H2 Codex home source selection. Does the isolated-home detection avoid self-referential CCB homes without rejecting legitimate user CODEX_HOME overrides? Check path-shape false positives and provider profile inheritance behavior.
```

Expected: no blockers; important findings must be fixed or explicitly deferred.

Review outcome: no blockers. Added `system_codex_home` to the public export list and kept symlink/`..` bypass as a documented edge case rather than broadening the patch.

## Shared Verification Gate

- [x] **Step 1: Run combined focused tests**

```bash
env PYTHONPATH=lib uvx pytest test/test_v2_ccbd_supervision_loop.py test/test_codex_home_sync.py test/test_provider_profiles.py -q
```

- [x] **Step 2: Run full pytest**

```bash
env PYTHONPATH=lib uvx pytest -q
```

- [x] **Step 3: Run diff hygiene**

```bash
git diff --check
```

- [ ] **Step 4: Install/merge canary plan**

After PR merge/install:

```bash
ccb doctor
ccb ask --wait --timeout 90 agent2 -- 'operational hardening post-install agent2 canary. Reply exactly: pong-op-hardening-agent2'
ccb ask --wait --timeout 180 agent3 -- 'operational hardening post-install agent3 canary. Reply exactly: pong-op-hardening-agent3'
```

Expected: install match true and exact replies.

## Deferred Follow-Ups

- Startup/attach latency instrumentation.
- Agent3 OAuth/silent block diagnostics.
- Restart/resubmit reason unification (`interrupted_by_restart` vs `ccbd_restart_requires_resubmit`).
- Global layout policy for untouched projects vs user-modified live tmux layouts.
- Old per-project ccbd/keeper cleanup policy.
