from __future__ import annotations

from .models import CodexPollState


def update_binding_refs(poll: CodexPollState, entry: dict[str, object]) -> None:
    entry_type = normalized_value(entry.get("entry_type"))
    payload_type = normalized_value(entry.get("payload_type"))
    entry_turn_id = normalized_value(entry.get("turn_id"))

    if payload_type == "task_started":
        observe_task_started(poll, turn_id=entry_turn_id)
        return
    if entry_type == "turn_context" or payload_type == "turn_context":
        observe_turn_context(poll, turn_id=entry_turn_id)
        return
    if not poll.anchor_seen:
        return
    backfill_bound_refs(poll, turn_id=entry_turn_id)


def bind_anchor_refs(poll: CodexPollState) -> None:
    if not poll.bound_turn_id and poll.current_turn_id:
        poll.bound_turn_id = poll.current_turn_id
    if poll.bound_turn_id and poll.current_turn_id == poll.bound_turn_id and poll.current_turn_started:
        poll.bound_turn_started = True


def assistant_entry_matches_bound_turn(poll: CodexPollState, entry: dict[str, object]) -> bool:
    entry_turn_id = normalized_value(entry.get("turn_id"))
    if poll.requires_turn_id and not entry_turn_id:
        if turnless_entry_is_inside_bound_turn(poll):
            return False
        poll.bound_turn_contaminated = True
        return False
    if not entry_turn_matches_required_identity(poll, entry_turn_id):
        return False
    if entry_turn_id and poll.bound_turn_id and entry_turn_id != poll.bound_turn_id:
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
    if not entry_turn_matches_required_identity(poll, entry_turn_id):
        return False
    if entry_turn_id:
        if poll.bound_turn_id and entry_turn_id != poll.bound_turn_id:
            return False
        if poll.bound_turn_contaminated:
            return False
        if not poll.bound_turn_id:
            poll.bound_turn_id = entry_turn_id
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


def observe_task_started(poll: CodexPollState, *, turn_id: str) -> None:
    if turn_id:
        poll.current_turn_id = turn_id
        poll.current_turn_started = True
    if poll.bound_turn_id and turn_id == poll.bound_turn_id:
        poll.bound_turn_started = True


def observe_turn_context(poll: CodexPollState, *, turn_id: str) -> None:
    if not turn_id:
        return
    if turn_id != poll.current_turn_id:
        poll.current_turn_started = False
    poll.current_turn_id = turn_id
    if poll.bound_turn_id and turn_id == poll.bound_turn_id and poll.current_turn_started:
        poll.bound_turn_started = True


def backfill_bound_refs(poll: CodexPollState, *, turn_id: str) -> None:
    if turn_id and not poll.bound_turn_id:
        poll.bound_turn_id = turn_id
    if poll.bound_turn_id and poll.current_turn_id == poll.bound_turn_id and poll.current_turn_started:
        poll.bound_turn_started = True


def entry_turn_matches_required_identity(poll: CodexPollState, entry_turn_id: str) -> bool:
    if poll.requires_turn_id and not entry_turn_id:
        poll.bound_turn_contaminated = True
        return False
    return True


def turnless_entry_is_inside_bound_turn(poll: CodexPollState) -> bool:
    if not poll.bound_turn_id:
        return False
    if poll.current_turn_id != poll.bound_turn_id:
        return False
    return bool(poll.current_turn_started and poll.bound_turn_started and not poll.bound_turn_contaminated)


def normalized_value(value: object) -> str:
    return str(value or "").strip()


__all__ = [
    "assistant_entry_matches_bound_turn",
    "bind_anchor_refs",
    "terminal_entry_matches_bound_turn",
    "update_binding_refs",
]
