from __future__ import annotations

from provider_execution.active import prepare_active_poll, prepare_active_poll_without_liveness
from provider_execution.base import ProviderPollResult, ProviderSubmission

from .binding_diag import maybe_emit_binding_diag
from .event_reading import read_entries
from .replay_runtime import is_wedge_condition, maybe_run_recovery
from .start import state_session_path
from .state_machine import (
    apply_session_rotation,
    build_poll_state,
    finalize_poll_result,
    handle_assistant_entry,
    handle_terminal_entry,
    handle_user_entry,
    update_binding_refs,
)


def poll_submission(submission: ProviderSubmission, *, now: str) -> ProviderPollResult | None:
    prepared = prepare_active_poll(submission, now=now)
    if prepared is None:
        return prepared
    if isinstance(prepared, ProviderPollResult):
        if not _should_try_dead_pane_recovery(submission, prepared):
            return prepared
        prepared_without_liveness = prepare_active_poll_without_liveness(submission, now=now)
        if prepared_without_liveness is None or isinstance(prepared_without_liveness, ProviderPollResult):
            return prepared
        return _poll_prepared_submission(submission, prepared_without_liveness, now=now)
    return _poll_prepared_submission(submission, prepared, now=now)


def _poll_prepared_submission(submission, prepared, *, now: str) -> ProviderPollResult | None:
    state = dict(submission.runtime_state.get("state") or {})
    pre_poll_state = dict(state)
    poll = build_poll_state(submission)
    state = poll_entry_batches(submission, poll, prepared.reader, state, now=now)
    maybe_emit_binding_diag(submission, poll, state, pre_poll_state=pre_poll_state)
    maybe_run_recovery(submission, poll, state=state, now=now)
    return finalize_poll_result(submission, poll, state=state)


def _should_try_dead_pane_recovery(submission: ProviderSubmission, result: ProviderPollResult) -> bool:
    decision = result.decision
    if decision is None or decision.reason != "pane_dead":
        return False
    try:
        poll = build_poll_state(submission)
    except Exception:
        return False
    return is_wedge_condition(poll)


def poll_entry_batches(submission, poll, reader, state, *, now: str):
    current_state = state
    while True:
        entries, current_state = read_entries(reader, current_state)
        apply_session_state(submission, poll, current_state, now=now)
        if not entries:
            break
        process_entry_batch(submission, poll, entries, now=now)
        if poll.reached_terminal:
            break
    return current_state


def apply_session_state(submission, poll, state, *, now: str) -> None:
    apply_session_rotation(
        submission,
        poll,
        new_session_path=state_session_path(state),
        now=now,
    )


def process_entry_batch(submission, poll, entries, *, now: str) -> None:
    for entry in entries:
        process_entry(submission, poll, entry, now=now)
        if poll.reached_terminal:
            break


def process_entry(submission, poll, entry, *, now: str) -> None:
    update_binding_refs(poll, entry)
    role = str(entry.get("role") or "").strip().lower()
    if role == "user":
        handle_user_entry(submission, poll, text=str(entry.get("text") or ""), now=now)
        return
    if not poll.anchor_seen:
        return
    if role == "assistant":
        handle_assistant_entry(submission, poll, entry, now=now)
        return
    handle_terminal_entry(submission, poll, entry, now=now)


__all__ = ["poll_submission"]
