from __future__ import annotations

from message_bureau import ReplyStore
from message_bureau.models import ReplyRecord, ReplyTerminalStatus
from mailbox_kernel.gc import compact_mailbox_jsonl
from storage.paths import PathLayout


def _reply(reply_id: str, text: str) -> ReplyRecord:
    return ReplyRecord(
        reply_id=reply_id,
        message_id=f"msg_{reply_id}",
        attempt_id=f"att_{reply_id}",
        agent_name="agent1",
        terminal_status=ReplyTerminalStatus.COMPLETED,
        reply=text,
        diagnostics={},
        finished_at="2026-05-03T00:00:00Z",
    )


def test_mailbox_gc_invalidates_reply_store_cache(tmp_path) -> None:
    layout = PathLayout(tmp_path / "repo")
    reply_store = ReplyStore(layout)
    reply_store.append(_reply("rep_1", "old"))
    assert reply_store.get_latest("rep_1").reply == "old"
    reply_store.append(_reply("rep_1", "new"))

    compact_mailbox_jsonl(
        layout,
        agent_names=("cmd",),
        now="2026-05-03T00:00:00Z",
        reply_cache_owner=reply_store._store,
    )

    assert reply_store.get_latest("rep_1").reply == "new"


def test_mailbox_gc_calls_reply_store_cache_invalidation(tmp_path) -> None:
    layout = PathLayout(tmp_path / "repo")
    reply_store = ReplyStore(layout)
    reply_store.append(_reply("rep_1", "body"))
    invalidated: list[object] = []

    class _CacheOwner:
        def invalidate(self, key):
            invalidated.append(key)

    compact_mailbox_jsonl(
        layout,
        agent_names=("cmd",),
        now="2026-05-03T00:00:00Z",
        reply_cache_owner=_CacheOwner(),
    )

    assert invalidated == [("replies", str(layout.ccbd_replies_path))]
