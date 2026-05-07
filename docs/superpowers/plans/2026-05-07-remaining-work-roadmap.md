# Remaining CCB v9 Work Roadmap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the remaining CCB v9 work in risk order without mixing unrelated provider, startup, and lifecycle changes.

**Architecture:** Keep each work item independently reviewable and shippable. Provider capability work establishes the multi-provider surface first; provider onboarding docs/tests then use that surface; startup/attach and cleanup hardening follow as separate operational tracks. Every implementation PR uses TDD, focused/full verification, local subagent review, and live CCB canary before install.

**Tech Stack:** Python, pytest via `env PYTHONPATH=lib uvx pytest`, CCB live canaries, GitHub PR flow against `fork/v6`.

---

## Skill Mapping

- `superpowers:using-git-worktrees`: before each implementation track, verify an isolated worktree/branch exists and the base is `fork/v6`.
- `superpowers:writing-plans`: this roadmap and each detailed per-track plan.
- `superpowers:systematic-debugging`: required for startup/attach latency, socket residue, OAuth silent block, queue metric ambiguity, and any failed canary.
- `superpowers:test-driven-development`: required before every behavior change.
- `superpowers:subagent-driven-development`: use for independent implementation/review tasks; default subagent effort can be low for mechanical review, higher only for architectural review.
- `superpowers:requesting-code-review`: after implementation, before PR merge/install. Use at least one local subagent review; when live CCB is healthy, also ask `agent3` for an independent adversarial review.
- `superpowers:receiving-code-review`: apply reviewer feedback only after verifying technical validity.
- `superpowers:verification-before-completion`: no completion/merge/install claims without fresh focused tests, full pytest, and live canary evidence.
- `github:github` / `github:gh-address-comments`: use for #34 review state, open PR metadata, and unresolved comments.
- `github:yeet` or GitHub connector PR tools: use only after local commit and verification are complete.

## Role and Effort Split

Use the lowest capable model/effort for each role, but keep final integration and risk decisions in this main session.

`spawn_agent` supports explicit reasoning effort for local subagents. CCB `ccb ask` does not currently expose `--model` or `--effort`; for CCB `agent2`/`agent3`, express the intended role and review depth in the prompt, then treat the reply as provider-specific evidence rather than a guaranteed effort setting.

| Role | Owner | Effort | Responsibilities | Do Not Delegate |
| --- | --- | --- | --- | --- |
| Coordinator / integrator | agent1 main session | current model | maintain roadmap, choose scope boundaries, integrate diffs, run final verification, open/merge PRs, install, live canary | none |
| Mechanical implementer | fresh local subagent | low | isolated one-file or two-file changes with explicit tests, docs-only edits, straightforward renderer/test updates | cross-cutting lifecycle changes, process cleanup, merge/install |
| Focused explorer | fresh local subagent | low | read-only code search, trace one call path, summarize PR state or file ownership | edits, commits |
| Adversarial code reviewer | fresh local subagent | low to medium | diff review for bugs, missing tests, regressions, unsafe cleanup, schema lock-in | final merge approval |
| Architecture/schema reviewer | fresh local subagent | medium to high | provider capability schema, provider onboarding contract, PR-S2 lifecycle design, registry boundaries | mechanical formatting or direct implementation unless separately scoped |
| Live provider reviewer | CCB `agent3` Claude | provider default | operational UX review, independent adversarial review through the real CCB route, Claude-specific lifecycle/paste/readiness concerns | source edits, commits, merge/install |
| Codex regression canary | CCB `agent2` Codex | provider default | codex-provider round-trip canary and regression signal after install/restart | self-target canaries from agent2 |
| GitHub operator | main session + GitHub connector | N/A | PR metadata, mergeability, comments, reviews, merge operation after verification | independent technical judgment |

### Per-Track Delegation Rules

- `PR-G1` provider capability doctor rework:
  - Schema spec: main session plus architecture/schema reviewer before implementation. This includes enum value set, `schema_version`, `ownership`, accessor signatures, disabled/env-only semantics, and doctor output contract.
  - Implementation: main session or low mechanical implementer only after schema spec sign-off, and only for isolated renderer/test/mechanical edits.
  - Reviews: architecture/schema reviewer plus `agent3` live provider reviewer.
  - Effort: medium/high for schema spec/review; low only for renderer/test mechanics after the schema is locked.
- `PR-G2` provider onboarding contract:
  - Contract authorship: main session or architecture/schema reviewer at medium+ effort. This is design work because it defines the adapter contract for multiple providers.
  - Implementation: low mechanical implementer may add the provider enumeration test skeleton only after the contract text is locked.
  - Reviews: medium architecture reviewer for contract completeness; `agent3` for Claude-provider operational gaps.
- `PR-O2` startup/attach latency instrumentation:
  - Investigation: main session leads systematic debugging.
  - Delegation: low explorer may gather logs and trace call paths.
  - Implementation: main session unless the patch is a narrow renderer/timing field.
  - Reviews: adversarial code reviewer for timing/observability regressions; operational review through live canary after install.
- `PR-O3` pytest/socket lifecycle cleanup:
  - Investigation and implementation: main session by default because cleanup/kill semantics are high-risk.
  - Delegation: read-only explorer for residue inventory; adversarial reviewer for safety boundaries.
- Provider home sync hardening residuals:
  - Scope list: broken symlink warning behavior, sentinel checksum, mtime/checksum skip, top-level `CLAUDE.md` symlink policy, threat model docs, e2e canary.
  - Implementation: low mechanical implementer per micro PR only for clearly mechanical behavior such as warn-once, checksum, or skip tests.
  - Reviews: low/medium code reviewer for mechanical changes; architecture reviewer for symlink policy, root-owned files, trust boundaries, or threat model changes.
- `PR-S2` session lifecycle isolation:
  - Implementation: main session or medium/high implementer only after a separate design plan.
  - Reviews: architecture reviewer and `agent3` reviewer are mandatory.
  - Caveat: `agent3` is both the Claude system under test and a Claude reviewer. Treat it as operational/provider-specific evidence, not as an independent architecture approval.

### Self-Target Canary Rules

- Do not validate `agent2` by asking `agent2` from an agent2 session.
- Do not validate `agent3` by asking `agent3` from an agent3 session.
- Do not over-weight `agent1` self-target canaries from this main agent1 panel. Prefer cross-agent canaries plus direct CCB replies visible in cmd.

### Review Gates

Every implementation PR must complete these gates before merge/install:

1. Focused tests pass locally.
2. Full pytest passes locally.
3. Local subagent adversarial review has no blocker or important finding.
4. If CCB live is healthy, `agent3` review or canary runs. If `agent3` is unhealthy, record the reason and do not treat the missing review as success.
5. After merge/install/restart, run at least one `agent2` and one `agent3` live canary. For Claude-touching changes, run `agent3` repeated canaries.

## Execution Order

0. `PR-G1`: #34 provider capability doctor rework is already merged; verify it is present in current `fork/v6` before building on it.
1. `PR-G2`: provider onboarding contract docs and test template.
2. `PR-O2`: startup/attach latency instrumentation and diagnosis.
3. `PR-O3`: pytest/socket lifecycle residue cleanup hardening.
4. `PR-H*`: provider home sync hardening residuals.
5. `PR-S2`: session lifecycle isolation for stale context.
6. Deferred diagnostics: OAuth silent block, cmd flush/redraw, queue metric semantics.

## Task 1: PR-G1 Provider Capability Doctor Verification

**Files:**
- Existing plan: `docs/superpowers/plans/2026-05-07-pr-34-provider-capability-rework.md`
- Read: `lib/cli/services/provider_home_sync.py`
- Read: `lib/cli/services/doctor_runtime/provider_home.py`
- Read: `lib/cli/render_runtime/ops_views_doctor.py`
- Test: `test/test_provider_home_sync.py`
- Test: `test/test_v2_cli_render.py`
- Test: `test/test_v2_tmux_cleanup_history.py`

- [ ] **Step 1: Refresh PR #34 state**

Run:

```bash
git fetch fork v6
gh pr view 34 --repo csg80752-png/claude_code_bridge --json number,state,baseRefName,headRefName,mergeStateStatus,commits,files,reviews,comments
```

Expected: #34 is merged into `fork/v6`. If it is not merged, stop and restore this task to the rework plan.

- [ ] **Step 2: Verify existing plan against current fork/v6**

Run:

```bash
sed -n '1,260p' docs/superpowers/plans/2026-05-07-pr-34-provider-capability-rework.md
git diff --stat fork/v6...HEAD
```

Expected: no hidden scope beyond provider capability doctor output, and current HEAD contains the #34 merge.

- [ ] **Step 3: Re-run focused tests before editing**

Run:

```bash
env PYTHONPATH=lib uvx pytest test/test_provider_home_sync.py test/test_claude_home_sync.py test/test_codex_home_sync.py test/test_v2_tmux_cleanup_history.py test/test_v2_cli_render.py -q
```

Expected: pass. If it fails, diagnose before starting PR-G2 because PR-G2 depends on #34's capability surface.

- [ ] **Step 4: Close G1**

Record the #34 merge SHA and focused test result in the PR-G2 plan or PR body. No code changes are expected in this task.

Expected: G1 verification is closed before starting PR-G2.

## Task 2: PR-G2 Provider Onboarding Contract

**Files:**
- Create: `docs/provider-onboarding-contract.md`
- Create or modify: `test/test_provider_onboarding_contract.py`
- Read: `lib/provider_core/catalog.py`
- Read: `lib/provider_execution/*`
- Read: `lib/provider_backends/*`

- [ ] **Step 1: Write the contract document**

Document required provider adapter behavior:
- start/resume
- submit/send prompt
- readiness and safe foreground checks
- completion terminal semantics
- failure diagnostics
- restart/recover behavior
- home/profile/env isolation
- doctor capability reporting

- [ ] **Step 2: Add contract test skeleton**

Create tests that enumerate currently supported providers and assert each provider has explicit capability/contract metadata.

Run:

```bash
env PYTHONPATH=lib uvx pytest test/test_provider_onboarding_contract.py -q
```

Expected RED before metadata is wired, GREEN after minimal registry fields are added.

- [ ] **Step 3: Verify no provider-specific hardcode creep**

Run:

```bash
rg -n 'provider ==|provider\\s+in|codex|claude|gemini|opencode|droid' lib/cli lib/provider_core lib/provider_execution
```

Expected: any remaining provider branch is documented as provider adapter boundary, not central orchestration logic.

## Task 3: PR-O2 Startup/Attach Latency Instrumentation

**Files:**
- Likely modify: `lib/cli/services/daemon.py`
- Likely modify: `lib/ccbd/daemon_process.py`
- Likely modify: `lib/ccbd/services/project_namespace_runtime/*`
- Likely test: `test/test_v2_phase2_entrypoint.py`
- Likely test: `test/test_ccbd_daemon_process.py`

- [ ] **Step 1: Capture baseline**

Run from `/home/speed/operational/statusline` after a restart window:

```bash
time ccb doctor >/tmp/ccb-doctor.out
time ccb ps >/tmp/ccb-ps.out
```

Expected: actual attach latency numbers before code changes.

- [ ] **Step 2: Add phase timing diagnostics**

Add structured timing for:
- daemon socket connect
- namespace attach readiness
- tmux list-windows/list-panes
- health ping
- first render

- [ ] **Step 3: TDD latency report rendering**

Add a test asserting doctor/ping can surface attach timing fields without breaking existing output.

Run:

```bash
env PYTHONPATH=lib uvx pytest test/test_v2_cli_render.py test/test_v2_phase2_entrypoint.py -q
```

Expected: focused pass.

## Task 4: PR-O3 Pytest/Socket Lifecycle Cleanup Hardening

**Files:**
- Likely modify: `lib/ccbd/daemon_process.py`
- Likely modify: `lib/ccbd/supervisor_runtime/*`
- Likely modify: test helpers in `test/test_v2_phase2_entrypoint.py`
- Likely test: `test/test_v2_ccbd_socket.py`
- Likely test: `test/test_v2_phase2_entrypoint.py`

- [ ] **Step 1: Reproduce residue safely**

Run:

```bash
find /tmp/pytest-of-speed -name 'ccbd.sock' | wc -l
ps -ef | rg '/tmp/pytest-of-speed|keeper_main.py|ccbd/main.py' || true
```

Expected: identify residue without touching statusline live project.

- [ ] **Step 2: Add cleanup tests**

Test only `/tmp/pytest-of-speed` scoped cleanup. Do not kill statusline or user project daemons.

- [ ] **Step 3: Implement cleanup**

Cleanup must be conservative:
- match pytest temp root.
- match project-local `.ccb/ccbd`.
- never kill current live project by default.
- emit diagnostics for killed pid/socket count.

## Task 5: Provider Home Sync Hardening Residuals

**Files:**
- Likely modify: `lib/cli/services/claude_home_sync.py`
- Likely modify: `lib/cli/services/provider_home_sync.py`
- Likely test: `test/test_claude_home_sync.py`
- Likely docs: `docs/provider-home-sync-threat-model.md`

- [ ] **Step 1: Split residuals into micro PRs**

Do not bundle all six. Use one PR per behavior:
- broken symlink warning behavior
- sentinel checksum
- mtime/checksum skip
- top-level `CLAUDE.md` symlink policy
- threat model docs
- e2e canary

- [ ] **Step 2: Apply TDD per micro PR**

Each micro PR must include a RED test and focused test run.

## Task 6: PR-S2 Session Lifecycle Isolation

**Files:**
- Likely modify: `lib/provider_backends/claude/*`
- Likely modify: `lib/provider_execution/*`
- Likely test: `test/test_claude_execution_polling.py`
- Likely test: `test/test_v2_phase2_entrypoint.py`

- [ ] **Step 1: Do not start until G1/G2 are merged**

Session lifecycle isolation is architectural. It depends on stable provider capability and onboarding contracts.

- [ ] **Step 2: Write separate design plan**

Create:

```bash
docs/superpowers/plans/YYYY-MM-DD-session-lifecycle-isolation.md
```

The plan must choose between:
- per-job fresh Claude session
- explicit `/new` lifecycle boundary
- session reuse with strict anchor isolation

## Global Verification Gate For Every PR

Before merge/install:

```bash
git diff --check
env PYTHONPATH=lib uvx pytest <focused tests> -q
env PYTHONPATH=lib uvx pytest -q
```

After install/restart:

```bash
ccb doctor | rg 'install_path|ccbd_daemon_install_path|ccbd_daemon_install_matches_current|ccbd_generation|ccbd_health'
ccb ask --wait --timeout 90 agent2 -- 'post-merge canary agent2. Reply exactly: pong-agent2'
ccb ask --wait --timeout 180 agent3 -- 'post-merge canary agent3. Reply exactly: pong-agent3'
```

Expected: daemon install match true, healthy, exact replies.
