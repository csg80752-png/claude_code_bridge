from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from provider_backends.codex.execution_runtime.binding_diag import (
    _predicate_blocker,
    maybe_emit_binding_diag,
    reset_emitted_for_test,
)
from provider_backends.codex.execution_runtime.state_machine_runtime.models import CodexPollState


@pytest.fixture(autouse=True)
def _reset_diag_gate() -> None:
    reset_emitted_for_test()
    yield
    reset_emitted_for_test()


def _make_wedged_poll() -> CodexPollState:
    return CodexPollState(
        anchor_seen=True,
        bound_turn_id="",
        current_turn_id="",
        requires_turn_id=True,
        bound_turn_contaminated=True,
        reply_buffer="",
        reached_terminal=False,
    )


def _write_session_jsonl(path: Path, *entries: dict) -> None:
    lines = [json.dumps(entry) for entry in entries]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_emits_warning_on_wedge(tmp_path, caplog):
    log_path = tmp_path / "session.jsonl"
    _write_session_jsonl(
        log_path,
        {"role": "user", "text": "hi"},
        {"role": "assistant", "text": "answer", "turn_id": "abc", "timestamp": "2026-05-04T00:00:00Z"},
    )
    submission = SimpleNamespace(job_id="job_test1")
    poll = _make_wedged_poll()
    state = {"log_path": log_path, "offset": 0}

    with caplog.at_level(logging.WARNING):
        maybe_emit_binding_diag(submission, poll, state)

    assert any("v8.4-diag binding-wedge" in record.getMessage() for record in caplog.records)
    msg = next(r.getMessage() for r in caplog.records if "v8.4-diag binding-wedge" in r.getMessage())
    assert "job=job_test1" in msg
    assert "bound_turn_contaminated=True" in msg
    assert "predicate_blocker=requires_turn_id_unsatisfied" in msg
    assert "lines_past_offset=2" in msg
    assert "role='assistant'" in msg


def test_one_shot_per_job_id(tmp_path, caplog):
    log_path = tmp_path / "session.jsonl"
    _write_session_jsonl(log_path, {"role": "user", "text": "x"})
    submission = SimpleNamespace(job_id="job_oneshot")
    poll = _make_wedged_poll()
    state = {"log_path": log_path, "offset": 0}

    with caplog.at_level(logging.WARNING):
        maybe_emit_binding_diag(submission, poll, state)
        maybe_emit_binding_diag(submission, poll, state)
        maybe_emit_binding_diag(submission, poll, state)

    wedge_records = [r for r in caplog.records if "v8.4-diag binding-wedge" in r.getMessage()]
    assert len(wedge_records) == 1


def test_skips_when_not_contaminated(tmp_path, caplog):
    log_path = tmp_path / "session.jsonl"
    _write_session_jsonl(log_path, {"role": "user", "text": "x"})
    submission = SimpleNamespace(job_id="job_clean")
    poll = CodexPollState(anchor_seen=True, bound_turn_contaminated=False, reply_buffer="")
    state = {"log_path": log_path, "offset": 0}

    with caplog.at_level(logging.WARNING):
        maybe_emit_binding_diag(submission, poll, state)

    assert not any("v8.4-diag binding-wedge" in r.getMessage() for r in caplog.records)


def test_skips_when_reply_buffer_nonempty(tmp_path, caplog):
    log_path = tmp_path / "session.jsonl"
    _write_session_jsonl(log_path, {"role": "user", "text": "x"})
    submission = SimpleNamespace(job_id="job_progressing")
    poll = CodexPollState(
        anchor_seen=True,
        bound_turn_contaminated=True,
        reply_buffer="partial answer in flight",
    )
    state = {"log_path": log_path, "offset": 0}

    with caplog.at_level(logging.WARNING):
        maybe_emit_binding_diag(submission, poll, state)

    assert not any("v8.4-diag binding-wedge" in r.getMessage() for r in caplog.records)


def test_skips_when_reached_terminal(tmp_path, caplog):
    log_path = tmp_path / "session.jsonl"
    _write_session_jsonl(log_path, {"role": "user", "text": "x"})
    submission = SimpleNamespace(job_id="job_terminal")
    poll = CodexPollState(
        anchor_seen=True,
        bound_turn_contaminated=True,
        reply_buffer="",
        reached_terminal=True,
    )
    state = {"log_path": log_path, "offset": 0}

    with caplog.at_level(logging.WARNING):
        maybe_emit_binding_diag(submission, poll, state)

    assert not any("v8.4-diag binding-wedge" in r.getMessage() for r in caplog.records)


def test_handles_missing_log_path(caplog):
    submission = SimpleNamespace(job_id="job_no_log")
    poll = _make_wedged_poll()
    state = {"log_path": None, "offset": -1}

    with caplog.at_level(logging.WARNING):
        maybe_emit_binding_diag(submission, poll, state)

    msg = next(r.getMessage() for r in caplog.records if "v8.4-diag binding-wedge" in r.getMessage())
    assert "log_path=None" in msg
    assert "lines_past_offset=-1" in msg
    assert "last_entry=no_log_path" in msg


def test_handles_missing_file(tmp_path, caplog):
    log_path = tmp_path / "missing.jsonl"
    submission = SimpleNamespace(job_id="job_missing_file")
    poll = _make_wedged_poll()
    state = {"log_path": log_path, "offset": 0}

    with caplog.at_level(logging.WARNING):
        maybe_emit_binding_diag(submission, poll, state)

    msg = next(r.getMessage() for r in caplog.records if "v8.4-diag binding-wedge" in r.getMessage())
    assert "job=job_missing_file" in msg
    assert "file_size=-1" in msg


def test_offset_at_eof_reports_zero_lines_past(tmp_path, caplog):
    log_path = tmp_path / "session.jsonl"
    _write_session_jsonl(
        log_path,
        {"role": "user", "text": "x"},
        {"role": "assistant", "text": "y", "turn_id": "t1"},
    )
    submission = SimpleNamespace(job_id="job_at_eof")
    poll = _make_wedged_poll()
    state = {"log_path": log_path, "offset": log_path.stat().st_size}

    with caplog.at_level(logging.WARNING):
        maybe_emit_binding_diag(submission, poll, state)

    msg = next(r.getMessage() for r in caplog.records if "v8.4-diag binding-wedge" in r.getMessage())
    assert "lines_past_offset=0" in msg


def test_skips_empty_job_id(caplog):
    submission = SimpleNamespace(job_id="")
    poll = _make_wedged_poll()
    state = {"log_path": None, "offset": -1}

    with caplog.at_level(logging.WARNING):
        maybe_emit_binding_diag(submission, poll, state)

    assert not any("v8.4-diag binding-wedge" in r.getMessage() for r in caplog.records)


def test_predicate_blocker_anchor_not_seen():
    poll = CodexPollState(anchor_seen=False, bound_turn_contaminated=True)
    assert _predicate_blocker(poll) == 'anchor_not_seen'


def test_predicate_blocker_requires_turn_id_unsatisfied():
    poll = CodexPollState(
        anchor_seen=True,
        requires_turn_id=True,
        bound_turn_contaminated=True,
        bound_turn_id="",
    )
    assert _predicate_blocker(poll) == 'requires_turn_id_unsatisfied'


def test_predicate_blocker_turn_id_mismatch():
    poll = CodexPollState(
        anchor_seen=True,
        bound_turn_id="bound",
        current_turn_id="other",
        bound_turn_started=True,
        bound_turn_contaminated=False,
    )
    assert _predicate_blocker(poll) == 'turn_id_mismatch'


def test_predicate_blocker_bound_turn_not_started():
    poll = CodexPollState(
        anchor_seen=True,
        bound_turn_id="bound",
        current_turn_id="bound",
        bound_turn_started=False,
        bound_turn_contaminated=False,
    )
    assert _predicate_blocker(poll) == 'bound_turn_not_started'


def test_predicate_blocker_no_bound_turn_id():
    poll = CodexPollState(
        anchor_seen=True,
        bound_turn_id="",
        bound_turn_contaminated=False,
        requires_turn_id=False,
    )
    assert _predicate_blocker(poll) == 'no_bound_turn_id'


def test_includes_envvar_value(monkeypatch, caplog):
    monkeypatch.setenv("CCB_CODEX_REQUIRES_TURN_ID", "1")
    submission = SimpleNamespace(job_id="job_envvar")
    poll = _make_wedged_poll()
    state = {"log_path": None, "offset": -1}

    with caplog.at_level(logging.WARNING):
        maybe_emit_binding_diag(submission, poll, state)

    msg = next(r.getMessage() for r in caplog.records if "v8.4-diag binding-wedge" in r.getMessage())
    assert "requires_turn_id_envvar='1'" in msg


def test_summary_truncates_long_text(tmp_path, caplog):
    log_path = tmp_path / "session.jsonl"
    long_text = "x" * 5000
    _write_session_jsonl(
        log_path,
        {"role": "assistant", "text": long_text, "turn_id": "t1", "timestamp": "ts"},
    )
    submission = SimpleNamespace(job_id="job_long_text")
    poll = _make_wedged_poll()
    state = {"log_path": log_path, "offset": 0}

    with caplog.at_level(logging.WARNING):
        maybe_emit_binding_diag(submission, poll, state)

    msg = next(r.getMessage() for r in caplog.records if "v8.4-diag binding-wedge" in r.getMessage())
    assert long_text not in msg
    assert "text_preview=" in msg
