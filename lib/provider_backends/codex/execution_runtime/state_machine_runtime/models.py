from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from completion.models import CompletionItem, CompletionItemKind
from provider_execution.base import ProviderSubmission
from provider_execution.common import build_item

CODEX_POLL_STATE_SCHEMA_VERSION = 2


@dataclass
class CodexPollState:
    schema_version: int = CODEX_POLL_STATE_SCHEMA_VERSION
    request_anchor: str = ""
    next_seq: int = 1
    anchor_seen: bool = False
    bound_turn_id: str = ""
    bound_task_id: str = ""
    reply_buffer: str = ""
    last_agent_message: str = ""
    last_final_answer: str = ""
    last_assistant_message: str = ""
    last_assistant_signature: str = ""
    session_path: str = ""
    current_turn_id: str = ""
    current_task_id: str = ""
    current_turn_started: bool = False
    bound_turn_started: bool = False
    bound_turn_contaminated: bool = False
    items: list[CompletionItem] = field(default_factory=list)
    reached_terminal: bool = False
    requires_task_id: bool = False
    task_id_probe_cache_key: str | None = None


def build_poll_state(submission: ProviderSubmission) -> CodexPollState:
    from .serialization import from_runtime_state

    return from_runtime_state(submission.runtime_state, fallback_request_anchor=submission.job_id)


def apply_session_rotation(
    submission: ProviderSubmission,
    poll: CodexPollState,
    *,
    new_session_path: str,
    now: str,
) -> None:
    if not new_session_path or new_session_path == poll.session_path:
        return
    poll.items.append(
        build_item(
            submission,
            kind=CompletionItemKind.SESSION_ROTATE,
            timestamp=now,
            seq=poll.next_seq,
            payload={
                "session_path": new_session_path,
                "provider_session_id": Path(new_session_path).stem,
            },
        )
    )
    poll.next_seq += 1
    poll.session_path = new_session_path
    poll.anchor_seen = bool(submission.runtime_state.get("no_wrap", False))
    poll.bound_turn_id = ""
    poll.bound_task_id = ""
    poll.current_turn_id = ""
    poll.current_task_id = ""
    poll.current_turn_started = False
    poll.bound_turn_started = False
    poll.bound_turn_contaminated = False
    poll.reply_buffer = ""
    poll.last_agent_message = ""
    poll.last_final_answer = ""
    poll.last_assistant_message = ""
    poll.last_assistant_signature = ""


__all__ = [
    "CODEX_POLL_STATE_SCHEMA_VERSION",
    "CodexPollState",
    "apply_session_rotation",
    "build_poll_state",
]
