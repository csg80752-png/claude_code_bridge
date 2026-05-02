from __future__ import annotations

from provider_core.protocol import REQ_ID_PREFIX
from completion.models import CompletionItemKind
from provider_execution.base import ProviderSubmission
from provider_execution.common import build_item

from .binding import bind_anchor_refs
from .models import CodexPollState


def handle_user_entry(
    submission: ProviderSubmission,
    poll: CodexPollState,
    *,
    text: str,
    now: str,
) -> None:
    if poll.anchor_seen:
        if _has_foreign_anchor(text, poll.request_anchor):
            poll.bound_turn_contaminated = True
        return
    if poll.request_anchor and f"{REQ_ID_PREFIX} {poll.request_anchor}" in text and not poll.anchor_seen:
        bind_anchor_refs(poll)
        payload: dict[str, object] = {}
        if poll.bound_turn_id:
            payload["turn_id"] = poll.bound_turn_id
        if poll.session_path:
            payload["session_path"] = poll.session_path
        poll.items.append(
            build_item(
                submission,
                kind=CompletionItemKind.ANCHOR_SEEN,
                timestamp=now,
                seq=poll.next_seq,
                payload=payload,
            )
        )
        poll.next_seq += 1
        poll.anchor_seen = True


def _has_foreign_anchor(text: str, request_anchor: str) -> bool:
    prefix = f"{REQ_ID_PREFIX} "
    expected = str(request_anchor or "").strip()
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith(prefix):
            continue
        found = stripped[len(prefix):].strip()
        if found and found != expected:
            return True
    return False


__all__ = ["handle_user_entry"]
