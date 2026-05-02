from __future__ import annotations

from .models import CodexPollState


def update_binding_refs(poll: CodexPollState, entry: dict[str, object]) -> None:
    entry_type = normalized_value(entry.get("entry_type"))
    payload_type = normalized_value(entry.get("payload_type"))
    entry_turn_id = normalized_value(entry.get("turn_id"))
    entry_task_id = normalized_value(entry.get("task_id"))

    if payload_type == "task_started":
        observe_task_started(poll, turn_id=entry_turn_id, task_id=entry_task_id)
        return
    if entry_type == "turn_context" or payload_type == "turn_context":
        observe_turn_context(poll, turn_id=entry_turn_id, task_id=entry_task_id)
        return
    if not poll.anchor_seen:
        return
    backfill_bound_refs(poll, turn_id=entry_turn_id, task_id=entry_task_id)


def bind_anchor_refs(poll: CodexPollState) -> None:
    if not poll.bound_turn_id and poll.current_turn_id:
        poll.bound_turn_id = poll.current_turn_id
    if not poll.bound_task_id and poll.current_task_id:
        poll.bound_task_id = poll.current_task_id
    if poll.bound_turn_id and poll.current_turn_id == poll.bound_turn_id and poll.current_turn_started:
        poll.bound_turn_started = True


def assistant_entry_matches_bound_turn(poll: CodexPollState, entry: dict[str, object]) -> bool:
    entry_turn_id = normalized_value(entry.get("turn_id"))
    entry_task_id = normalized_value(entry.get("task_id"))
    if entry_turn_id and poll.bound_turn_id and entry_turn_id != poll.bound_turn_id:
        return False
    if not entry_task_matches_bound(poll, entry_task_id):
        return False
    if poll.bound_turn_contaminated:
        return False
    if not poll.bound_turn_id:
        return True
    if poll.current_turn_id:
        if poll.current_turn_id != poll.bound_turn_id:
            return False
        if not poll.bound_turn_started:
            return False
    return True


def terminal_entry_matches_bound_turn(poll: CodexPollState, entry: dict[str, object]) -> bool:
    entry_turn_id = normalized_value(entry.get("turn_id"))
    entry_task_id = normalized_value(entry.get("task_id"))
    if entry_turn_id:
        if poll.bound_turn_id and entry_turn_id != poll.bound_turn_id:
            return False
        if not entry_task_matches_bound(poll, entry_task_id):
            return False
        if poll.bound_turn_contaminated:
            return False
        if not poll.bound_turn_id:
            poll.bound_turn_id = entry_turn_id
        if not poll.bound_task_id and entry_task_id:
            poll.bound_task_id = entry_task_id
        if poll.current_turn_id == poll.bound_turn_id and poll.current_turn_started:
            poll.bound_turn_started = True
        return True
    if not poll.bound_turn_id:
        return True
    if poll.bound_turn_contaminated:
        return False
    if poll.current_turn_id:
        if poll.current_turn_id != poll.bound_turn_id:
            return False
        if not poll.bound_turn_started:
            return False
    return True


def observe_task_started(poll: CodexPollState, *, turn_id: str, task_id: str) -> None:
    if turn_id:
        poll.current_turn_id = turn_id
        poll.current_turn_started = True
    if task_id:
        poll.current_task_id = task_id
    if poll.bound_turn_id and turn_id == poll.bound_turn_id:
        poll.bound_turn_started = True
        if task_id and not poll.bound_task_id:
            poll.bound_task_id = task_id


def observe_turn_context(poll: CodexPollState, *, turn_id: str, task_id: str) -> None:
    if not turn_id:
        return
    if turn_id != poll.current_turn_id:
        poll.current_turn_started = False
    poll.current_turn_id = turn_id
    if task_id:
        poll.current_task_id = task_id
    if poll.bound_turn_id and turn_id == poll.bound_turn_id and poll.current_turn_started:
        poll.bound_turn_started = True


def backfill_bound_refs(poll: CodexPollState, *, turn_id: str, task_id: str) -> None:
    if turn_id and not poll.bound_turn_id:
        poll.bound_turn_id = turn_id
    if task_id and not poll.bound_task_id:
        poll.bound_task_id = task_id
    if poll.bound_turn_id and poll.current_turn_id == poll.bound_turn_id and poll.current_turn_started:
        poll.bound_turn_started = True


def entry_task_matches_bound(poll: CodexPollState, entry_task_id: str) -> bool:
    return not (entry_task_id and poll.bound_task_id and entry_task_id != poll.bound_task_id)


def normalized_value(value: object) -> str:
    return str(value or "").strip()


__all__ = [
    "assistant_entry_matches_bound_turn",
    "bind_anchor_refs",
    "terminal_entry_matches_bound_turn",
    "update_binding_refs",
]
