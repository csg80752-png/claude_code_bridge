from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

from completion.models import CompletionItemKind, CompletionSourceKind
from provider_backends.codex.execution_runtime.polling import poll_submission
from provider_backends.codex.execution_runtime.replay_runtime import (
    REPLAY_FAIL_FOREIGN_ANCHOR,
    replay_anchor_bound,
)
from provider_backends.codex.execution_runtime.replay_runtime import anchor_scan, quarantine
from provider_execution.base import ProviderSubmission


class _NoNewEntriesReader:
    def try_get_entries(self, state: dict[str, object]):
        return [], state


class _DeadBackend:
    def is_alive(self, _pane_id: str) -> bool:
        return False


def _submission(*, runtime_state: dict[str, object] | None = None) -> ProviderSubmission:
    return ProviderSubmission(
        job_id="job_exact",
        agent_name="agent1",
        provider="codex",
        accepted_at="2026-05-05T00:00:00Z",
        ready_at="2026-05-05T00:00:00Z",
        source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
        reply="",
        runtime_state=dict(runtime_state or {}),
    )


def _write_jsonl(path: Path, entries: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + "\n")


def _user(job_id: str, *, turn_id: str) -> dict[str, object]:
    return {
        "type": "event_msg",
        "turn_id": turn_id,
        "payload": {
            "type": "user_message",
            "role": "user",
            "message": f"CCB_REQ_ID: {job_id}\n\nprompt",
        },
    }


def _assistant(text: str, *, turn_id: str) -> dict[str, object]:
    return {
        "type": "event_msg",
        "turn_id": turn_id,
        "payload": {"type": "agent_message", "role": "assistant", "message": text},
    }


def _terminal(text: str, *, turn_id: str) -> dict[str, object]:
    return {
        "type": "event_msg",
        "turn_id": turn_id,
        "payload": {"type": "task_complete", "turn_id": turn_id, "last_agent_message": text},
    }


def test_deferred_wedge_ticks_persist_until_third_empty_poll_replays(tmp_path, monkeypatch) -> None:
    log_path = tmp_path / "session.jsonl"
    _write_jsonl(
        log_path,
        [
            _user("job_exact", turn_id="turn-exact"),
            _assistant("RECOVERED", turn_id="turn-exact"),
            _terminal("RECOVERED", turn_id="turn-exact"),
        ],
    )
    state = {"log_path": str(log_path), "offset": log_path.stat().st_size}
    submission = _submission(
        runtime_state={
            "state": state,
            "request_anchor": "job_exact",
            "anchor_seen": True,
            "requires_turn_id": True,
            "bound_turn_contaminated": True,
            "reply_buffer": "",
            "consecutive_wedge_ticks": 0,
        }
    )

    monkeypatch.setattr(
        "provider_backends.codex.execution_runtime.polling_runtime.prepare_active_poll",
        lambda submission, now: SimpleNamespace(reader=_NoNewEntriesReader()),
    )
    monkeypatch.setattr(
        "provider_backends.codex.execution_runtime.polling_runtime.apply_session_rotation",
        lambda submission, poll, new_session_path, now: None,
    )

    result = None
    for tick in range(3):
        result = poll_submission(submission, now=f"2026-05-05T00:00:0{tick}Z")
        assert result is not None
        submission = result.submission

    assert submission.runtime_state["consecutive_wedge_ticks"] == 0
    assert submission.runtime_state["bound_turn_contaminated"] is False
    assert submission.reply == "RECOVERED"
    assert result is not None
    assert [item.kind for item in result.items] == [
        CompletionItemKind.ASSISTANT_CHUNK,
        CompletionItemKind.TURN_BOUNDARY,
    ]


def test_dead_pane_does_not_preempt_replay_eligible_wedge(tmp_path, monkeypatch) -> None:
    log_path = tmp_path / "session.jsonl"
    _write_jsonl(
        log_path,
        [
            _user("job_exact", turn_id="turn-exact"),
            _assistant("RECOVERED", turn_id="turn-exact"),
            _terminal("RECOVERED", turn_id="turn-exact"),
        ],
    )
    state = {"log_path": str(log_path), "offset": log_path.stat().st_size}
    submission = _submission(
        runtime_state={
            "mode": "active",
            "reader": _NoNewEntriesReader(),
            "backend": _DeadBackend(),
            "pane_id": "%dead",
            "state": state,
            "request_anchor": "job_exact",
            "anchor_seen": True,
            "requires_turn_id": True,
            "bound_turn_contaminated": True,
            "reply_buffer": "",
            "consecutive_wedge_ticks": 2,
        }
    )

    monkeypatch.setattr(
        "provider_backends.codex.execution_runtime.polling_runtime.apply_session_rotation",
        lambda submission, poll, new_session_path, now: None,
    )

    result = poll_submission(submission, now="2026-05-05T00:00:03Z")

    assert result is not None
    assert [item.kind for item in result.items] == [
        CompletionItemKind.ASSISTANT_CHUNK,
        CompletionItemKind.TURN_BOUNDARY,
    ]
    assert result.submission.reply == "RECOVERED"
    assert result.submission.runtime_state["bound_turn_contaminated"] is False


def test_anchor_scan_matches_exact_req_id_and_treats_prefixed_id_as_foreign(tmp_path) -> None:
    log_path = tmp_path / "session.jsonl"
    _write_jsonl(
        log_path,
        [
            _user("job_exact_suffix", turn_id="turn-suffix"),
            _assistant("WRONG", turn_id="turn-suffix"),
            _terminal("WRONG", turn_id="turn-suffix"),
            _user("job_exact", turn_id="turn-exact"),
            _assistant("RIGHT", turn_id="turn-exact"),
            _terminal("RIGHT", turn_id="turn-exact"),
        ],
    )

    result = replay_anchor_bound(log_path, request_anchor="job_exact")

    assert result.success
    assert result.turn_id == "turn-exact"
    assert result.assistant_entries[0]["text"] == "RIGHT"

    expected_marker = "CCB_REQ_ID: job_exact"
    foreign_prefix = "CCB_REQ_ID: "
    assert anchor_scan._has_foreign_anchor(
        "CCB_REQ_ID: job_exact_suffix",
        expected_marker,
        foreign_prefix,
    )

    _write_jsonl(
        log_path,
        [
            _user("job_exact", turn_id="turn-exact"),
            _user("job_exact_suffix", turn_id="turn-suffix"),
            _assistant("RIGHT", turn_id="turn-exact"),
            _terminal("RIGHT", turn_id="turn-exact"),
        ],
    )
    result = replay_anchor_bound(log_path, request_anchor="job_exact")
    assert not result.success
    assert result.status == REPLAY_FAIL_FOREIGN_ANCHOR


def test_quarantine_size_cap_eviction_does_not_double_count_deleted_siblings(tmp_path, monkeypatch) -> None:
    root = tmp_path / "quarantine"
    root.mkdir()
    monkeypatch.setenv("CCB_QUARANTINE_SIZE_CAP_BYTES", "70")

    sizes = {
        "old.jsonl": 1,
        "old.manifest.json": 49,
        "mid.jsonl": 25,
        "mid.manifest.json": 25,
        "new.jsonl": 25,
        "new.manifest.json": 25,
    }
    for index, (name, size) in enumerate(sizes.items(), start=1):
        path = root / name
        path.write_bytes(b"x" * size)
        os.utime(path, (index, index))

    quarantine._evict_size_cap(root, now=10.0)

    on_disk_total = sum(path.stat().st_size for path in root.iterdir() if path.is_file())
    assert on_disk_total <= 70
    assert sorted(path.name for path in root.iterdir()) == ["new.jsonl", "new.manifest.json"]
