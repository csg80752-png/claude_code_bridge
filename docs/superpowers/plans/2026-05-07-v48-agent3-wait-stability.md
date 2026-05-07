# v48 Agent3 Wait Stability Plan

**Goal:** Stop intermittent `agent3` failed/empty replies caused by a Claude prompt that is visibly staged in the input box but was never submitted.

## Skill Mapping

- `superpowers:writing-plans`: capture the execution plan and acceptance gates.
- `superpowers:systematic-debugging`: prove root cause before changing code.
- `superpowers:test-driven-development`: add a RED regression test before production changes.
- `superpowers:requesting-code-review`: request subagent adversarial review after implementation and after review fixes.
- `superpowers:verification-before-completion`: require focused tests, full pytest, and live canaries before completion.

## Evidence

- Four sequential `agent3` wait canaries completed with exact replies.
- The fifth canary failed with empty reply and `claude_runtime_not_ready`.
- The Claude pane showed the requested text still staged at the prompt line:
  `❯ v48 agent3 wait stability round 5`
- A follow-up `agent3` job failed the same way.

## Root Cause

The Claude provider can have `prompt_sent=False` while the prompt text has already been pasted into the Claude input box. Polling then waits for readiness, sees the pane is not ready, and eventually fails the job as `claude_runtime_not_ready`. No completion event can occur because the staged prompt was never submitted.

## Implementation

- In `provider_backends.claude.execution_runtime.polling`, detect a staged prompt only when:
  - `prompt_sent` is false.
  - The pane exposes `get_pane_content` and `send_key`.
  - The current bottom prompt line starts with Claude's `❯` input marker.
  - The bottom screen has no busy/blocking markers.
  - The staged tail exactly matches the final non-empty line of `prompt_text`.
  - The staged tail is not short or a common unsafe command such as `yes`, `ok`, or `continue`.
- If those checks pass, send `Enter`, mark `prompt_sent=True`, and record `staged_prompt_submitted_at`.
- Otherwise, keep the existing readiness/prompt delivery path unchanged.

## Tests

- Add a RED/GREEN regression test for submitting a staged Claude prompt.
- Add negative tests for:
  - stale prompt above a busy marker.
  - visible prompt text that is not the final prompt suffix.
  - ambiguous short/common staged tails such as `continue`.
- Stabilize existing full-suite timing flakes that blocked verification:
  - Increase the fake heartbeat job duration enough to keep active execution visible during doctor/ping assertions.
  - Scope the cmd-mailbox socket test away from visible cmd-pane delivery.

## Acceptance

- `env PYTHONPATH=lib uvx pytest test/test_claude_execution_polling.py -q`
- `env PYTHONPATH=lib uvx pytest test/test_v2_phase2_entrypoint.py::test_ccb_long_running_job_keeps_heartbeat_and_doctor_healthy test/test_v2_ccbd_socket.py::test_ccbd_cmd_sender_routes_reply_into_cmd_mailbox -q -vv`
- `git diff --check`
- `env PYTHONPATH=lib uvx pytest -q`
- Subagent adversarial review has no blockers or important findings.
- After install/restart, `agent1`, `agent2`, and repeated `agent3` round-trips pass.
