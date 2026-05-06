# v8.5 Issue F: Agent3 Context Bleed Decision

Date: 2026-05-06

## Decision

Do not close Issue F as fixed in v8.5. Classify it as a P1 follow-up and route
the root fix into the v9 agent-message-management cycle.

## Evidence

Agent3, running through the Claude provider, preserved stale matrix-session
context across fresh `ccb ask` work. A later relay attempt reported progress
that did not match the underlying command result:

- observed job: `job_92e549201a23`
- actual result: `ccb ask failed`
- failure detail: `ccbd exited before ready with code 1`
- operator impact: agent3 could present stale or hallucinated relay status

This is not the same class as Issue E. Cmd visible delivery recovered, but
agent3's provider session state can still contaminate task behavior and reports.

## Options Considered

### A. Inject `/new` before every Claude-provider ask

Pros:

- simple operational model
- strong isolation between asks

Cons:

- provider-specific slash command coupling leaks into dispatch policy
- can race with an already-running task or blocked Claude session
- `/new` did not immediately clear the observed blocked session during live
  testing

### B. Isolate Claude provider execution by task/session lifecycle

Pros:

- fixes the root cause at the provider/session boundary
- aligns with the v9 roadmap's `ProviderHealthSnapshot`, attempt lifecycle, and
  message-management separation
- can represent stale session, blocked runtime, and failed relay as first-class
  attempt states

Cons:

- larger design/implementation than a v8.5 close-out patch
- needs explicit policy for resume vs fresh session

### C. Keep manual `/new` workaround only

Pros:

- no new code
- acceptable for one-off operator recovery

Cons:

- does not protect automated relay workflows
- leaves agent3 reports untrusted under stale-context conditions

## Selected Path

Choose Option B for root fix. Keep Option C as the short-term workaround.
Do not use Option A as the final design, though an explicit operator-triggered
`autonew` command can remain useful.

## v8.5 Closure Rule

v8.5 can close with Issue F marked:

- status: deferred
- priority: P1
- known workaround: manually reset or restart agent3 when stale context is
  suspected
- restriction: do not treat agent3 relay self-reports as ground truth; verify
  via `ccb pend <job_id>` or mailbox state

## v9 Entry Criteria

The v9 `agent-message-management-roadmap` cycle should include Issue F as a
provider/session isolation requirement:

- fresh-attempt policy for Claude-provider asks
- explicit stale-session detection
- relay result grounding in durable job state
- operator-visible attempt state for `stalled`, `runtime_dead`,
  `terminal_failed`, and `indeterminate`
