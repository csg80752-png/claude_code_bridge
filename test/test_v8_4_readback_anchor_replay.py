"""v8.4 Wave 2 Lane C — Issue #1 readback fix tests.

Spec: docs/v8.4-plan.md §"Issue 1 — readback binding rehydration",
"A vs C decision rule" (i)/(ii)/(iii)/(iv), and §Acceptance criteria
"Replay/rebind tests prove no binding to old turns, latest unrelated turns,
or foreign CCB_REQ_ID anchors."

Diag evidence: reports/20260505T112345Z-v8.4-pr8-diag-capture-report.md
documents two live shapes recoverable via Shape A.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from completion.models import CompletionItemKind, CompletionSourceKind
from provider_backends.codex.execution_runtime.replay_runtime import (
    DEFAULT_WEDGE_TICK_THRESHOLD,
    REPLAY_FAIL_ANCHOR_NOT_FOUND,
    REPLAY_FAIL_FOREIGN_ANCHOR,
    REPLAY_FAIL_LATEST_UNRELATED,
    REPLAY_FAIL_NO_TERMINAL,
    REPLAY_FAIL_ORPHAN_TURN,
    REPLAY_OK,
    is_wedge_condition,
    maybe_run_recovery,
    quarantine_root,
    replay_anchor_bound,
    update_wedge_counter,
    write_quarantine,
)
from provider_backends.codex.execution_runtime.state_machine_runtime.models import CodexPollState
from provider_execution.base import ProviderSubmission


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_quarantine_root(tmp_path, monkeypatch):
    """Force quarantine writes into pytest's tmp_path so home dir is never
    touched by these tests."""
    monkeypatch.setenv("CCB_QUARANTINE_ROOT", str(tmp_path / ".gstack" / "quarantine"))
    yield


@pytest.fixture(autouse=True)
def _stable_threshold_env(monkeypatch):
    monkeypatch.delenv("CCB_REPLAY_WEDGE_TICK_THRESHOLD", raising=False)
    yield


def _make_submission(*, job_id: str = "job_test", agent: str = "agent2") -> ProviderSubmission:
    return ProviderSubmission(
        job_id=job_id,
        agent_name=agent,
        provider="codex",
        accepted_at="2026-05-05T11:00:00Z",
        ready_at="2026-05-05T11:00:00Z",
        source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
        reply="",
        runtime_state={"state": {}, "inbound_event_id": "iev_replay_test"},
    )


def _wedged_poll(*, request_anchor: str, ticks: int | None = None) -> CodexPollState:
    return CodexPollState(
        request_anchor=request_anchor,
        anchor_seen=True,
        bound_turn_id="",
        current_turn_id="",
        requires_turn_id=True,
        bound_turn_contaminated=True,
        reply_buffer="",
        reached_terminal=False,
        consecutive_wedge_ticks=ticks if ticks is not None else 0,
    )


def _user_anchor_entry(job_id: str, *, turn_id: str = "", message: str | None = None) -> dict:
    body = message if message is not None else f"CCB_REQ_ID: {job_id}\n\n[CCB_HEARTBEAT_TEST]"
    entry: dict = {
        "type": "event_msg",
        "timestamp": "2026-05-05T11:19:00.000Z",
        "payload": {"type": "user_message", "role": "user", "message": body},
    }
    if turn_id:
        entry["turn_id"] = turn_id
    return entry


def _assistant_entry(text: str, *, turn_id: str, ts: str = "2026-05-05T11:19:01.000Z") -> dict:
    return {
        "type": "event_msg",
        "timestamp": ts,
        "turn_id": turn_id,
        "payload": {"type": "agent_message", "role": "assistant", "message": text},
    }


def _terminal_entry(*, turn_id: str, last: str, ts: str = "2026-05-05T11:19:02.000Z") -> dict:
    return {
        "type": "event_msg",
        "timestamp": ts,
        "turn_id": turn_id,
        "payload": {"type": "task_complete", "turn_id": turn_id, "last_agent_message": last},
    }


def _task_started_entry(*, turn_id: str, ts: str = "2026-05-05T11:18:59.000Z") -> dict:
    return {
        "type": "event_msg",
        "timestamp": ts,
        "payload": {"type": "task_started", "turn_id": turn_id},
    }


def _turn_context_entry(*, turn_id: str, ts: str = "2026-05-05T11:18:59.100Z") -> dict:
    return {
        "type": "turn_context",
        "timestamp": ts,
        "payload": {"turn_id": turn_id},
    }


def _response_item_user_entry(job_id: str, *, message: str | None = None) -> dict:
    body = message if message is not None else f"CCB_REQ_ID: {job_id}\n\n[CCB_HEARTBEAT_TEST]"
    return {
        "type": "response_item",
        "timestamp": "2026-05-05T11:19:00.000Z",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": body}],
        },
    }


def _turnless_assistant_entry(text: str, *, ts: str = "2026-05-05T11:19:01.000Z") -> dict:
    return {
        "type": "event_msg",
        "timestamp": ts,
        "payload": {"type": "agent_message", "role": "assistant", "message": text},
    }


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# Decision rule (i): anchor present → binds turn from anchor entry's turn_id
# Decision rule (ii) + (iii): assistant + terminal entries collected
# Decision rule (iv): N-tick gate enforced via update_wedge_counter
# ---------------------------------------------------------------------------


def test_case01_successful_anchor_bound_replay(tmp_path):
    """Case 1 — happy path: contaminated binding + valid jsonl recovers reply.

    Mirrors the diag report's emission 3 (job_426a340a5924, fresh codex CLI
    session with anchor + agent_message + task_complete present).
    """
    log_path = tmp_path / "session.jsonl"
    _write_jsonl(
        log_path,
        [
            _user_anchor_entry("job_test", turn_id="turn-A"),
            _assistant_entry("PONG_DIAG", turn_id="turn-A"),
            _terminal_entry(turn_id="turn-A", last="PONG_DIAG"),
        ],
    )

    submission = _make_submission()
    poll = _wedged_poll(request_anchor="job_test", ticks=DEFAULT_WEDGE_TICK_THRESHOLD - 1)
    state = {"log_path": log_path, "offset": log_path.stat().st_size}

    outcome = maybe_run_recovery(submission, poll, state=state, now="2026-05-05T11:19:50Z")

    assert outcome.action == "replayed"
    assert outcome.reason == REPLAY_OK
    assert outcome.turn_id == "turn-A"
    assert poll.reached_terminal is True
    assert poll.bound_turn_contaminated is False
    assert poll.replay_in_progress is False
    assert poll.consecutive_wedge_ticks == 0
    assert poll.bound_turn_id == "turn-A"
    assert poll.last_assistant_message == "PONG_DIAG"
    assert poll.reply_buffer == "PONG_DIAG"
    kinds = [item.kind for item in poll.items]
    assert CompletionItemKind.ASSISTANT_CHUNK in kinds
    assert CompletionItemKind.TURN_BOUNDARY in kinds
    boundary = next(item for item in poll.items if item.kind is CompletionItemKind.TURN_BOUNDARY)
    assert boundary.payload["replay"] == "anchor_bound"
    assert boundary.payload["last_agent_message"] == "PONG_DIAG"


def test_replay_accepts_current_codex_unkeyed_messages_inside_turn_context(tmp_path):
    """Current Codex logs key the turn on task_started/turn_context and the
    terminal event, while user and assistant message entries may be unkeyed."""
    log_path = tmp_path / "session.jsonl"
    _write_jsonl(
        log_path,
        [
            _task_started_entry(turn_id="turn-live"),
            _turn_context_entry(turn_id="turn-live"),
            _response_item_user_entry("job_test"),
            _user_anchor_entry("job_test"),
            _turnless_assistant_entry("PONG_LIVE"),
            _terminal_entry(turn_id="turn-live", last="PONG_LIVE"),
        ],
    )

    result = replay_anchor_bound(log_path, request_anchor="job_test")

    assert result.status == REPLAY_OK
    assert result.turn_id == "turn-live"
    assert [entry["text"] for entry in result.assistant_entries] == ["PONG_LIVE"]
    assert result.terminal_entry is not None


def test_replay_ignores_duplicate_anchor_payload_with_embedded_req_id(tmp_path):
    """A single Codex user turn is logged as both response_item and event_msg.
    If that payload contains another literal CCB_REQ_ID in the prompt body, it
    is not a separate provider turn and must not be treated as a foreign anchor.
    """
    log_path = tmp_path / "session.jsonl"
    body = "CCB_REQ_ID: job_test\n\nCCB_REQ_ID: statusline_fresh_agent2_after_model_restart\n\nhello"
    _write_jsonl(
        log_path,
        [
            _task_started_entry(turn_id="turn-live"),
            _turn_context_entry(turn_id="turn-live"),
            _response_item_user_entry("job_test", message=body),
            _user_anchor_entry("job_test", message=body),
            _turnless_assistant_entry("PONG_LIVE"),
            _terminal_entry(turn_id="turn-live", last="PONG_LIVE"),
        ],
    )

    result = replay_anchor_bound(log_path, request_anchor="job_test")

    assert result.status == REPLAY_OK
    assert result.turn_id == "turn-live"
    assert [entry["text"] for entry in result.assistant_entries] == ["PONG_LIVE"]
    assert result.terminal_entry is not None


# ---------------------------------------------------------------------------
# Misbinding rejections (acceptance §"replay/rebind tests prove no binding
# to old turns, latest unrelated turns, or foreign CCB_REQ_ID anchors")
# ---------------------------------------------------------------------------


def test_case02_misbinding_rejected_foreign_anchor(tmp_path):
    """Case 2 — foreign anchor between ours and the assistant entry."""
    log_path = tmp_path / "session.jsonl"
    _write_jsonl(
        log_path,
        [
            _user_anchor_entry("job_test", turn_id="turn-X"),
            _user_anchor_entry("job_other", turn_id="turn-Y"),
            _assistant_entry("PONG_FOR_OTHER", turn_id="turn-Y"),
            _terminal_entry(turn_id="turn-Y", last="PONG_FOR_OTHER"),
        ],
    )
    submission = _make_submission()
    poll = _wedged_poll(request_anchor="job_test", ticks=DEFAULT_WEDGE_TICK_THRESHOLD - 1)
    state = {"log_path": log_path, "offset": log_path.stat().st_size}

    outcome = maybe_run_recovery(submission, poll, state=state, now="2026-05-05T11:20:00Z")

    assert outcome.action == "abandoned"
    assert outcome.reason == REPLAY_FAIL_FOREIGN_ANCHOR
    assert poll.reached_terminal is True
    assert "PONG_FOR_OTHER" not in poll.reply_buffer
    assert poll.last_assistant_message == ""
    abort = next(item for item in poll.items if item.kind is CompletionItemKind.TURN_ABORTED)
    assert abort.payload["replay_failure"] == REPLAY_FAIL_FOREIGN_ANCHOR
    assert outcome.quarantine_jsonl is not None
    assert outcome.quarantine_manifest is not None


def test_case03_misbinding_rejected_orphan_turn(tmp_path):
    """Case 3 — anchor present but no assistant entry exists."""
    log_path = tmp_path / "session.jsonl"
    _write_jsonl(
        log_path,
        [
            _user_anchor_entry("job_test", turn_id="turn-A"),
            # no assistant entry; only an unrelated terminal that we will
            # also reject because it has a different turn_id
            _terminal_entry(turn_id="turn-B", last=""),
        ],
    )
    submission = _make_submission()
    poll = _wedged_poll(request_anchor="job_test", ticks=DEFAULT_WEDGE_TICK_THRESHOLD - 1)
    state = {"log_path": log_path, "offset": log_path.stat().st_size}

    outcome = maybe_run_recovery(submission, poll, state=state, now="2026-05-05T11:21:00Z")

    assert outcome.action == "abandoned"
    assert outcome.reason == REPLAY_FAIL_ORPHAN_TURN
    assert poll.reached_terminal is True
    abort = next(item for item in poll.items if item.kind is CompletionItemKind.TURN_ABORTED)
    assert abort.payload["replay_failure"] == REPLAY_FAIL_ORPHAN_TURN


def test_case04_misbinding_rejected_latest_unrelated_turn(tmp_path):
    """Case 4 — assistant entry exists but for a turn unrelated to ours."""
    log_path = tmp_path / "session.jsonl"
    _write_jsonl(
        log_path,
        [
            _user_anchor_entry("job_test", turn_id="turn-A"),
            _assistant_entry("UNRELATED_REPLY", turn_id="turn-Z"),
            _terminal_entry(turn_id="turn-Z", last="UNRELATED_REPLY"),
        ],
    )
    submission = _make_submission()
    poll = _wedged_poll(request_anchor="job_test", ticks=DEFAULT_WEDGE_TICK_THRESHOLD - 1)
    state = {"log_path": log_path, "offset": log_path.stat().st_size}

    outcome = maybe_run_recovery(submission, poll, state=state, now="2026-05-05T11:22:00Z")

    assert outcome.action == "abandoned"
    assert outcome.reason == REPLAY_FAIL_LATEST_UNRELATED
    assert poll.last_assistant_message == ""
    assert "UNRELATED_REPLY" not in poll.reply_buffer


# ---------------------------------------------------------------------------
# Replay-over-started reply skipped (gate (iv) precondition)
# ---------------------------------------------------------------------------


def test_case05_replay_over_started_reply_skipped(tmp_path):
    """Case 5 — reply_buffer != "" precondition fails → replay path no-ops.

    The wedge predicate (binding_diag._is_wedge_condition / replay
    is_wedge_condition) explicitly excludes this state.
    """
    log_path = tmp_path / "session.jsonl"
    _write_jsonl(
        log_path,
        [
            _user_anchor_entry("job_test", turn_id="turn-A"),
            _assistant_entry("BUFFERED", turn_id="turn-A"),
            _terminal_entry(turn_id="turn-A", last="BUFFERED"),
        ],
    )
    submission = _make_submission()
    poll = CodexPollState(
        request_anchor="job_test",
        anchor_seen=True,
        bound_turn_contaminated=True,
        reply_buffer="partial in flight",
        consecutive_wedge_ticks=10,
    )
    state = {"log_path": log_path, "offset": log_path.stat().st_size}

    outcome = maybe_run_recovery(submission, poll, state=state, now="2026-05-05T11:23:00Z")

    assert outcome.action == "noop"
    assert outcome.reason == "not_wedged"
    assert poll.reached_terminal is False
    assert poll.replay_in_progress is False
    assert poll.consecutive_wedge_ticks == 0


# ---------------------------------------------------------------------------
# Race protection — concurrent ticks cannot re-enter replay mid-stream
# ---------------------------------------------------------------------------


def test_case06_replay_in_progress_short_circuits(tmp_path):
    """Case 6 — ``replay_in_progress`` flag blocks concurrent re-entry."""
    log_path = tmp_path / "session.jsonl"
    _write_jsonl(
        log_path,
        [
            _user_anchor_entry("job_test", turn_id="turn-A"),
            _assistant_entry("ANSWER", turn_id="turn-A"),
            _terminal_entry(turn_id="turn-A", last="ANSWER"),
        ],
    )
    submission = _make_submission()
    poll = _wedged_poll(request_anchor="job_test", ticks=DEFAULT_WEDGE_TICK_THRESHOLD - 1)
    poll.replay_in_progress = True  # simulate prior tick still running
    state = {"log_path": log_path, "offset": log_path.stat().st_size}

    outcome = maybe_run_recovery(submission, poll, state=state, now="2026-05-05T11:24:00Z")

    assert outcome.action == "noop"
    assert outcome.reason == "replay_in_progress"
    # Reentrancy guard must not deliver a second copy of the reply.
    assert poll.reached_terminal is False
    assert poll.last_assistant_message == ""
    assert poll.reply_buffer == ""
    # Flag remains True because the original tick still owns it.
    assert poll.replay_in_progress is True


# ---------------------------------------------------------------------------
# Quarantine: env vars honored, manifest + raw tail written
# ---------------------------------------------------------------------------


def test_case07_quarantine_on_abandon_writes_jsonl_and_manifest(tmp_path, monkeypatch):
    """Case 7 — abandon path produces both files; env vars are honored."""
    monkeypatch.setenv("CCB_QUARANTINE_TAIL_ENTRIES", "3")
    monkeypatch.setenv("CCB_QUARANTINE_TTL_DAYS", "1")
    monkeypatch.setenv("CCB_QUARANTINE_SIZE_CAP_BYTES", "1000000")

    log_path = tmp_path / "session.jsonl"
    rows = [
        _user_anchor_entry("job_test", turn_id="turn-A"),
        # No assistant entry → orphan_turn rejection.
        _terminal_entry(turn_id="turn-A", last=""),
        # Plus a few extra noise rows so the tail-3 trim is meaningful.
        {"type": "event_msg", "timestamp": "2026-05-05T11:19:05.000Z",
         "payload": {"type": "task_started", "turn_id": "turn-A"}},
        {"type": "event_msg", "timestamp": "2026-05-05T11:19:06.000Z",
         "payload": {"type": "turn_context", "turn_id": "turn-A"}},
        {"type": "event_msg", "timestamp": "2026-05-05T11:19:07.000Z",
         "payload": {"type": "noop"}},
    ]
    _write_jsonl(log_path, rows)
    submission = _make_submission()
    poll = _wedged_poll(request_anchor="job_test", ticks=DEFAULT_WEDGE_TICK_THRESHOLD - 1)
    state = {"log_path": log_path, "offset": log_path.stat().st_size}

    outcome = maybe_run_recovery(submission, poll, state=state, now="2026-05-05T11:25:00Z")

    assert outcome.action == "abandoned"
    assert outcome.quarantine_jsonl is not None
    assert outcome.quarantine_manifest is not None

    jsonl_path = outcome.quarantine_jsonl
    manifest_path = outcome.quarantine_manifest
    assert jsonl_path.parent == manifest_path.parent
    assert quarantine_root() == jsonl_path.parent

    # Tail-3 honored.
    written = [line for line in jsonl_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(written) == 3

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["agent"] == "agent2"
    assert manifest["job_id"] == "job_test"
    assert manifest["inbound_event_id"] == "iev_replay_test"
    assert manifest["session_path"] == str(log_path)
    assert manifest["entry_count"] == 3
    assert manifest["failure_reason"] in {
        REPLAY_FAIL_ORPHAN_TURN,
        REPLAY_FAIL_NO_TERMINAL,
    }
    assert manifest["tail_entries_limit"] == 3


def test_case07b_quarantine_ttl_evicts_old_files(tmp_path, monkeypatch):
    """TTL eviction runs on each write; old files disappear."""
    monkeypatch.setenv("CCB_QUARANTINE_TTL_DAYS", "1")
    root = tmp_path / ".gstack" / "quarantine"
    monkeypatch.setenv("CCB_QUARANTINE_ROOT", str(root))
    root.mkdir(parents=True, exist_ok=True)

    stale_jsonl = root / "agent2-job_old-20260101T000000Z.jsonl"
    stale_jsonl.write_text("{}\n", encoding="utf-8")
    stale_manifest = root / "agent2-job_old-20260101T000000Z.manifest.json"
    stale_manifest.write_text("{}\n", encoding="utf-8")
    # Backdate mtimes 30 days.
    import os, time as _time
    old_ts = _time.time() - 30 * 86400
    os.utime(stale_jsonl, (old_ts, old_ts))
    os.utime(stale_manifest, (old_ts, old_ts))

    record = write_quarantine(
        agent="agent2",
        job_id="job_fresh",
        inbound_event_id="iev_x",
        session_path=str(tmp_path / "session.jsonl"),
        offset=0,
        failure_reason="ttl_test",
        raw_lines=["{\"a\":1}\n", "{\"b\":2}\n"],
    )
    assert record is not None
    assert not stale_jsonl.exists()
    assert not stale_manifest.exists()


# ---------------------------------------------------------------------------
# Quarantine permission-denied — abandon proceeds, no exception escapes
# ---------------------------------------------------------------------------


def test_case08_quarantine_permission_denied_warning_then_proceed(tmp_path, monkeypatch, caplog):
    """Case 8 — mkdir fails → log a warning, abandon still proceeds."""

    bad_root = tmp_path / "blocked" / "quarantine"
    monkeypatch.setenv("CCB_QUARANTINE_ROOT", str(bad_root))

    # Force mkdir to PermissionError; pathlib calls os.makedirs internally.
    real_mkdir = Path.mkdir

    def _denied(self, *args, **kwargs):
        if str(self).startswith(str(tmp_path / "blocked")):
            raise PermissionError(13, "permission denied", str(self))
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", _denied)

    log_path = tmp_path / "session.jsonl"
    _write_jsonl(
        log_path,
        [
            _user_anchor_entry("job_test", turn_id="turn-A"),
            # Orphan: no assistant.
        ],
    )
    submission = _make_submission()
    poll = _wedged_poll(request_anchor="job_test", ticks=DEFAULT_WEDGE_TICK_THRESHOLD - 1)
    state = {"log_path": log_path, "offset": log_path.stat().st_size}

    with caplog.at_level(logging.WARNING):
        outcome = maybe_run_recovery(submission, poll, state=state, now="2026-05-05T11:26:00Z")

    # Abandon ran, even without quarantine I/O.
    assert outcome.action == "abandoned"
    assert outcome.quarantine_jsonl is None
    assert outcome.quarantine_manifest is None
    assert poll.reached_terminal is True
    assert any("quarantine mkdir permission denied" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# No-stuck-INCOMPLETE: post-recovery the execution always reaches terminal
# ---------------------------------------------------------------------------


def test_case09_no_stuck_incomplete_after_replay_or_abandon(tmp_path):
    """Case 9 — every recovery path leaves ``reached_terminal=True``."""

    # 9a — replay path:
    log_path = tmp_path / "ok.jsonl"
    _write_jsonl(
        log_path,
        [
            _user_anchor_entry("job_test", turn_id="turn-A"),
            _assistant_entry("REPLY", turn_id="turn-A"),
            _terminal_entry(turn_id="turn-A", last="REPLY"),
        ],
    )
    poll = _wedged_poll(request_anchor="job_test", ticks=DEFAULT_WEDGE_TICK_THRESHOLD - 1)
    maybe_run_recovery(
        _make_submission(),
        poll,
        state={"log_path": log_path, "offset": log_path.stat().st_size},
        now="2026-05-05T11:27:00Z",
    )
    assert poll.reached_terminal is True
    assert poll.consecutive_wedge_ticks == 0
    assert poll.replay_in_progress is False

    # 9b — abandon path (foreign anchor):
    foreign_log = tmp_path / "foreign.jsonl"
    _write_jsonl(
        foreign_log,
        [
            _user_anchor_entry("job_test", turn_id="turn-A"),
            _user_anchor_entry("job_other", turn_id="turn-Z"),
            _assistant_entry("OTHER", turn_id="turn-Z"),
        ],
    )
    poll2 = _wedged_poll(request_anchor="job_test", ticks=DEFAULT_WEDGE_TICK_THRESHOLD - 1)
    maybe_run_recovery(
        _make_submission(),
        poll2,
        state={"log_path": foreign_log, "offset": foreign_log.stat().st_size},
        now="2026-05-05T11:27:01Z",
    )
    assert poll2.reached_terminal is True


# ---------------------------------------------------------------------------
# Pre-anchor entries are not used as evidence even if turn_id matches
# (acceptance §"replay/rebind tests prove no binding to old turns")
# ---------------------------------------------------------------------------


def test_case10_pre_anchor_entries_not_used_as_evidence(tmp_path):
    """Case 10 — entries BEFORE our anchor are not collected even if their
    turn_id happens to coincide with the anchor's turn_id.

    This is the explicit "no binding to old turns" assertion. Construct a
    jsonl where an old assistant entry on turn-A precedes our anchor (also
    on turn-A); the anchor-bound replay must only collect entries strictly
    after the anchor offset.
    """
    log_path = tmp_path / "session.jsonl"
    _write_jsonl(
        log_path,
        [
            # Old turn from a prior request (re-using turn-A by coincidence).
            _assistant_entry("OLD_LEAKED_REPLY", turn_id="turn-A",
                             ts="2026-05-04T22:00:00.000Z"),
            _terminal_entry(turn_id="turn-A", last="OLD_LEAKED_REPLY",
                            ts="2026-05-04T22:00:01.000Z"),
            # Our anchor + fresh turn for THIS job, also on turn-A.
            _user_anchor_entry("job_test", turn_id="turn-A"),
            _assistant_entry("FRESH_REPLY", turn_id="turn-A"),
            _terminal_entry(turn_id="turn-A", last="FRESH_REPLY"),
        ],
    )
    submission = _make_submission()
    poll = _wedged_poll(request_anchor="job_test", ticks=DEFAULT_WEDGE_TICK_THRESHOLD - 1)
    state = {"log_path": log_path, "offset": log_path.stat().st_size}

    outcome = maybe_run_recovery(submission, poll, state=state, now="2026-05-05T11:28:00Z")

    assert outcome.action == "replayed"
    assert poll.last_assistant_message == "FRESH_REPLY"
    assert "OLD_LEAKED_REPLY" not in poll.reply_buffer


# ---------------------------------------------------------------------------
# N-tick gate (decision rule iv) — replay deferred until threshold hits
# ---------------------------------------------------------------------------


def test_n_tick_gate_defers_until_threshold(tmp_path):
    log_path = tmp_path / "session.jsonl"
    _write_jsonl(
        log_path,
        [
            _user_anchor_entry("job_test", turn_id="turn-A"),
            _assistant_entry("REPLY", turn_id="turn-A"),
            _terminal_entry(turn_id="turn-A", last="REPLY"),
        ],
    )
    submission = _make_submission()
    poll = _wedged_poll(request_anchor="job_test")
    state = {"log_path": log_path, "offset": log_path.stat().st_size}

    # First N-1 ticks must defer.
    for i in range(DEFAULT_WEDGE_TICK_THRESHOLD - 1):
        outcome = maybe_run_recovery(submission, poll, state=state, now=f"2026-05-05T11:30:0{i}Z")
        assert outcome.action == "deferred", f"tick {i}"

    # Final tick crosses threshold and replays.
    outcome = maybe_run_recovery(submission, poll, state=state, now="2026-05-05T11:30:09Z")
    assert outcome.action == "replayed"


def test_n_tick_gate_resets_when_wedge_clears(tmp_path):
    poll = _wedged_poll(request_anchor="job_test", ticks=2)
    poll.bound_turn_contaminated = False  # wedge clears
    update_wedge_counter(poll)
    assert poll.consecutive_wedge_ticks == 0


# ---------------------------------------------------------------------------
# Direct unit tests of the anchor scanner so misbinding rules are pinned
# ---------------------------------------------------------------------------


def test_replay_anchor_scan_returns_anchor_offset(tmp_path):
    log_path = tmp_path / "scan.jsonl"
    _write_jsonl(
        log_path,
        [
            _assistant_entry("LEAK", turn_id="leak", ts="2026-05-04T00:00:00Z"),
            _user_anchor_entry("job_test", turn_id="turn-A"),
            _assistant_entry("REPLY", turn_id="turn-A"),
            _terminal_entry(turn_id="turn-A", last="REPLY"),
        ],
    )
    result = replay_anchor_bound(log_path, request_anchor="job_test")
    assert result.success
    assert result.turn_id == "turn-A"
    assert result.anchor_offset > 0
    # Pre-anchor leak entry must not be in the collected assistant entries.
    assert all("LEAK" not in (e.get("text") or "") for e in result.assistant_entries)


def test_replay_anchor_scan_rejects_when_no_anchor(tmp_path):
    log_path = tmp_path / "scan.jsonl"
    _write_jsonl(log_path, [_assistant_entry("nope", turn_id="turn-X")])
    result = replay_anchor_bound(log_path, request_anchor="job_test")
    assert not result.success
    assert result.status == REPLAY_FAIL_ANCHOR_NOT_FOUND


def test_replay_anchor_scan_handles_missing_file():
    result = replay_anchor_bound(None, request_anchor="job_test")
    assert not result.success


# ---------------------------------------------------------------------------
# Wedge-condition predicate parity (matches binding_diag's gate)
# ---------------------------------------------------------------------------


def test_is_wedge_condition_matches_diag_gate():
    poll = CodexPollState(
        anchor_seen=True,
        bound_turn_contaminated=True,
        reply_buffer="",
        reached_terminal=False,
    )
    assert is_wedge_condition(poll) is True

    poll.reply_buffer = "x"
    assert is_wedge_condition(poll) is False
    poll.reply_buffer = ""
    poll.reached_terminal = True
    assert is_wedge_condition(poll) is False
    poll.reached_terminal = False
    poll.bound_turn_contaminated = False
    assert is_wedge_condition(poll) is False
