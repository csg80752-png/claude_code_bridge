# v9 Plan Engineering Review

Date: 2026-05-06

## Status

Accepted with three required guardrails:

1. classify paste-collapse before implementation
2. keep v9 plan output in `reports/v9/plan-eng-review.md`
3. preserve v8.5 live and pytest regression baselines

## Source Of Truth

`fork/v6` is the source of truth after the v8.5 merge sequence.

Merged critical path:

```text
#11 -> #12 -> #14 -> #13 -> #15 -> #16
```

Meaning:

- #11: cmd visible delivery and duplicate-main guard
- #12: mailbox `active` tolerant read
- #14: `pending_cmd_replies` metric semantics
- #13: Codex readback hardening
- #15: v8.4/v8.5 closure docs, Issue F deferred, Issue G opened
- #16: sync-codex-home micro-PR

## Cleanup Step

Before starting v9 implementation, align the local worktree to `fork/v6`.

Run a final status check first:

```bash
git status --short --untracked-files=all
```

Expected dirty items are the already-merged local leftovers from #11-#16.
Then clean:

```bash
git fetch fork v6
git reset --hard fork/v6
git clean -fd
git status --short
```

## Input Documents

Read these before writing or changing v9 code:

- `reports/v8.5/v8.4-closure.md`
- `reports/v8.5/issue-f-agent3-context-bleed-decision.md`
- `docs/agent-message-management-roadmap.md`

Keep `docs/agent-message-management-roadmap.md` as an input document unless a
later documentation PR intentionally updates it.

## v9 Scope

### 1. Claude Paste-Collapse Permanent Fix

Problem: Claude-provider pane submission can require manual Enter under some
states, which blocks reliable automation and can obscure agent3 debugging.

Decision gate:

```text
Is paste-collapse a blocker for Issue F debugging?
```

Run one live proof before implementation:

- send a fresh task to agent3 without manual Enter
- confirm whether it submits and produces durable readback without operator
  intervention

Branch:

- YES: create PR-F0 as a small paste-collapse hotfix first
- NO: fold paste/transport hardening into PR-F1 or the Issue F implementation

### 2. Issue G: `cmd` As First-Class `ccb ask` Target

Problem: the operator/cmd pane is the intended cmd endpoint, but:

```text
ccb ask cmd "..."
```

currently fails with:

```text
unknown agent: cmd
```

This is separate from Issue A. Issue A was cmd pane discovery/injection target
selection. Issue G is submit-target routing.

Expected result:

- `ccb ask cmd "..."` reaches the operator/cmd pane through the CCB control
  plane
- no direct tmux send-keys workaround is required for normal cmd targeting
- existing agent targets still work

Primary engineering risks:

- `MessageEnvelope.to_agent` currently normalizes non-`all` targets as agents
- `JobRecord` and `JobEvent` normalize `target_name` through agent-only paths
- dispatcher submit planning is agent-only
- cmd target jobs must not accidentally require provider runtime execution

### 3. Issue F: Agent3 Claude-Provider Context Bleed

Problem: agent3 can preserve stale Claude session context and report relay
state that does not match durable job state.

Selected root-fix direction:

- provider/session lifecycle isolation
- durable job state as ground truth
- explicit stale-session and indeterminate attempt state

Do not rely on automatic slash-command reset as the final architecture. Manual
reset remains a workaround only.

## Proposed PR Sequence

```text
[PR-F0?] paste-collapse hotfix
PR-G    cmd target routing
PR-F1   Claude paste/transport hardening
PR-F2   session isolation / stale context lifecycle
```

Notes:

- PR-F0 is conditional on the live paste-collapse proof.
- PR-G should run before Issue F because it is smaller, user-facing, and
  isolated to routing/model paths.
- PR-F1 and PR-F2 can be split further if provider lifecycle changes become too
  broad.

## Regression Guards

### Baseline Test Guard

The combined baseline from v8.5 was:

```text
1769 passed, 17 skipped
```

Each v9 PR should maintain or increase the passing test count. Any intentional
skip or removal must be justified in the PR body.

### Live Canary Guard

Before each v9 PR merge, run at least one live round-trip:

```bash
ccb ask agent2 "v9 live canary. Reply exactly: pong-v9-agent2"
ccb pend <job_id>
```

Acceptance:

- job reaches `status=completed`
- reply text is exact
- cmd visible delivery still works
- `ccb ping ccbd` remains healthy

### PR-G Specific Guard

For cmd target routing:

- `ccb ask cmd "..."` must submit without `unknown agent: cmd`
- the message must surface in the operator/cmd pane
- `ccb ask agent1`, `ccb ask agent2`, and `ccb ask agent3` must still normalize
  and route as before
- cmd target support must not regress mailbox owner handling for existing cmd
  reply delivery

## Open Follow-Ups

- Decide whether v9 updates `docs/agent-message-management-roadmap.md` or keeps
  all execution details in reports.
- Decide whether Issue G is completed before v9 proper or remains the first v9
  PR.
- Keep operational stderr burn-in separate from v9 unless fresh post-v25 firing
  is observed.
