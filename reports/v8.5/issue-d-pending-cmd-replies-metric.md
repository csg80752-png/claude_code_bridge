# v8.5 Issue D: `pending_cmd_replies` Metric Semantics

Date: 2026-05-06

## Decision

`pending_cmd_replies` is no longer an acceptance metric for cmd visible delivery.
Treat it as a backlog/debug counter only.

## Reason

Live v8.5 verification showed cmd visible delivery recovered while
`pending_cmd_replies` remained frozen at 358. Fresh replies were injected and
visible in the cmd pane, but old queued task-reply events remained in the
mailbox backlog. That means the counter conflates at least two states:

- queued replies that have never been surfaced to cmd
- old replies already surfaced to cmd but not acknowledged or garbage-collected

The first state is operationally important. The second is mailbox hygiene and
does not imply a cmd UX outage.

## Acceptance Metrics

Use these as the v8.5 cmd delivery acceptance signals:

1. `ccb ask <agent>` completes with `status=completed` and exact reply text.
2. The cmd pane receives visible delivery for fresh replies.
3. `.ccb/ccbd/cmd-delivered-cache.jsonl` receives a new `reply_id` entry for
   the fresh reply.
4. `ccb ping ccbd` remains `health=healthy`, `socket_connectable=True`, and
   `pid_alive=True`.
5. No new post-restart `PermissionError`, `ValueError: 'active'`, or
   `bound_turn_contaminated=True` entries are observed for the tested job.

## Non-Acceptance Signal

Do not require `pending_cmd_replies` to monotonically drain to zero for v8.5
closure. A stable nonzero value is acceptable when fresh visible delivery works
and durable delivered-cache records are advancing.

## Future Metric Split

For a later cleanup cycle, split the counter into two explicit values:

- `queued_uninjected_cmd_replies`: queued cmd task replies with no durable
  delivered-cache hit.
- `awaiting_cmd_ack_cmd_replies`: queued cmd task replies already recorded in
  `cmd-delivered-cache.jsonl`.

Only `queued_uninjected_cmd_replies` should block canary acceptance.
