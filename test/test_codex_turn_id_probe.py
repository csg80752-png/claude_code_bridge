from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from ccbd.api_models import DeliveryScope, JobRecord, JobStatus, MessageEnvelope
from completion.models import CompletionItemKind, CompletionSourceKind
from provider_backends.codex.execution_runtime.polling import poll_submission
from provider_backends.codex.execution_runtime.start import start_active_submission
from provider_backends.codex.launcher_runtime.turn_id_probe import (
    BROKEN_STATE,
    CODEX_TURN_ID_PROBE_SCHEMA_VERSION,
    PROBE_TIMEOUT_SECONDS,
    CodexTurnIdProbeResult,
    apply_configured_startup_turn_id_probe,
    build_probe_cache_key,
    configured_startup_turn_id_probe,
    probe_from_completion_log,
)
from provider_execution.base import ProviderRuntimeContext, ProviderSubmission


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "codex_turn_id_probe"


def _submission(runtime_state: dict[str, object] | None = None) -> ProviderSubmission:
    return ProviderSubmission(
        job_id="job_probe",
        agent_name="agent1",
        provider="codex",
        accepted_at="2026-05-02T00:00:00Z",
        ready_at="2026-05-02T00:00:00Z",
        source_kind=CompletionSourceKind.PROTOCOL_EVENT_STREAM,
        reply="",
        runtime_state={
            "state": {},
            "request_anchor": "job_probe",
            "anchor_seen": False,
            **(runtime_state or {}),
        },
    )


def _job() -> JobRecord:
    return JobRecord(
        job_id="job_probe",
        submission_id="sub_probe",
        agent_name="agent1",
        provider="codex",
        request=MessageEnvelope(
            project_id="proj",
            to_agent="agent1",
            from_actor="cmd",
            body="hello",
            task_id=None,
            reply_to=None,
            message_type="ask",
            delivery_scope=DeliveryScope.SINGLE,
        ),
        status=JobStatus.RUNNING,
        terminal_decision=None,
        cancel_requested_at=None,
        created_at="2026-05-02T00:00:00Z",
        updated_at="2026-05-02T00:00:00Z",
    )


def _clear_turn_id_probe_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "CCB_CODEX_TURN_ID_PROBE_LOG",
        "CCB_CODEX_TURN_ID_PROBE_BINARY",
        "CCB_CODEX_TURN_ID_PROBE_VERSION",
        "CCB_CODEX_TURN_ID_PROBE_MTIME_NS",
        "CCB_CODEX_TURN_ID_PROBE_DISABLED",
        "CCB_CODEX_TASK_ID_PROBE_DISABLED",
    ):
        monkeypatch.delenv(key, raising=False)


def _write_fake_codex(binary_dir: Path, *, turn_id: str = "turn-auto-probe") -> Path:
    binary_dir.mkdir(parents=True, exist_ok=True)
    binary = binary_dir / "codex"
    binary.write_text(
        """#!/bin/sh
if [ "$1" = "--ask-for-approval" ]; then
  shift 2
fi
if [ "$1" = "--version" ]; then
  echo "codex fake 1.2.3"
  exit 0
fi
if [ "$1" = "exec" ]; then
  cat >/dev/null
  printf '%s\n' '{"timestamp":"2026-05-01T03:17:31.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"TURN_ID_PLACEHOLDER","last_agent_message":"probe complete","completed_at":1777605451,"duration_ms":42,"time_to_first_token_ms":7}}'
  exit 0
fi
echo "unexpected fake codex invocation: $*" >&2
exit 2
""".replace("TURN_ID_PLACEHOLDER", turn_id),
        encoding="utf-8",
    )
    binary.chmod(0o755)
    return binary


def _write_fake_codex_with_session_log(binary_dir: Path, *, turn_id: str = "turn-session-probe") -> Path:
    binary_dir.mkdir(parents=True, exist_ok=True)
    binary = binary_dir / "codex"
    binary.write_text(
        """#!/bin/sh
if [ "$1" = "--ask-for-approval" ]; then
  shift 2
fi
if [ "$1" = "--version" ]; then
  echo "codex fake 1.2.3"
  exit 0
fi
if [ "$1" = "exec" ]; then
  cat >/dev/null
  thread_id="thread-session-probe"
  sessions_root="${CODEX_HOME:-$HOME/.codex}/sessions/2026/05/03"
  mkdir -p "$sessions_root"
  session_log="$sessions_root/rollout-2026-05-03T00-00-00-${thread_id}.jsonl"
  printf '%s\n' '{"type":"thread.started","thread_id":"thread-session-probe"}'
  printf '%s\n' '{"type":"turn.started"}'
  printf '%s\n' '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"probe"}}'
  printf '%s\n' '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}'
  printf '%s\n' '{"timestamp":"2026-05-01T03:17:31.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"TURN_ID_PLACEHOLDER","last_agent_message":"probe complete","completed_at":1777605451,"duration_ms":42,"time_to_first_token_ms":7}}' > "$session_log"
  exit 0
fi
echo "unexpected fake codex invocation: $*" >&2
exit 2
""".replace("TURN_ID_PLACEHOLDER", turn_id),
        encoding="utf-8",
    )
    binary.chmod(0o755)
    return binary


def _poll_entries(monkeypatch, entries: list[dict[str, object]], *, runtime_state: dict[str, object] | None = None):
    queue = [dict(entry) for entry in entries]

    def fake_read_entries(reader, state):
        del reader
        index = int(state.get("index", 0))
        if index >= len(queue):
            return [], state
        return [queue[index]], {"index": index + 1}

    monkeypatch.setattr(
        "provider_backends.codex.execution_runtime.polling_runtime.prepare_active_poll",
        lambda submission, now: type("Prepared", (), {"reader": object()})(),
    )
    monkeypatch.setattr(
        "provider_backends.codex.execution_runtime.polling_runtime.read_entries",
        fake_read_entries,
    )
    monkeypatch.setattr(
        "provider_backends.codex.execution_runtime.polling_runtime.apply_session_rotation",
        lambda submission, poll, new_session_path, now: None,
    )
    monkeypatch.setattr(
        "provider_backends.codex.execution_runtime.polling_runtime.state_session_path",
        lambda state: "",
    )
    return poll_submission(_submission(runtime_state=runtime_state), now="2026-05-02T00:00:01Z")


def test_codex_turn_id_probe_result_exports_schema_version() -> None:
    result = CodexTurnIdProbeResult.pass_result(
        binary_realpath="/usr/bin/codex",
        version="codex 1.0.0",
        binary_mtime_ns=123,
        completion_log_path="/tmp/codex-probe.jsonl",
        turn_id="turn-probe",
        probe_timeout_seconds=PROBE_TIMEOUT_SECONDS,
    )

    record = result.to_record()

    assert record["schema_version"] == CODEX_TURN_ID_PROBE_SCHEMA_VERSION
    assert record["record_type"] == "codex_turn_id_probe_result"
    assert record["state"] == "PASS"
    assert record["turn_id"] == "turn-probe"


def test_codex_turn_id_probe_against_real_codex_cli_schema() -> None:
    fixture = FIXTURE_DIR / "golden-task-complete-with-turn-id.jsonl"

    result = probe_from_completion_log(
        fixture,
        binary_realpath="/usr/bin/codex",
        version="codex 1.0.0",
        binary_mtime_ns=123,
    )

    assert result.state == "PASS"
    assert result.turn_id == "turn_probe_fixture"
    assert result.completion_log_path == str(fixture)


def test_codex_turn_id_probe_fail_closes_when_completion_omits_turn_id() -> None:
    fixture = FIXTURE_DIR / "golden-task-complete-without-turn-id.jsonl"

    result = probe_from_completion_log(
        fixture,
        binary_realpath="/usr/bin/codex",
        version="codex 1.0.0",
        binary_mtime_ns=123,
    )

    assert result.state == BROKEN_STATE
    assert result.turn_id is None
    assert "turn_id" in result.error


def test_codex_turn_id_probe_cache_key_changes_on_version_or_mtime() -> None:
    base = build_probe_cache_key("/usr/bin/codex", "codex 1.0.0", 123)

    assert build_probe_cache_key("/usr/bin/codex", "codex 1.0.1", 123) != base
    assert build_probe_cache_key("/usr/bin/codex", "codex 1.0.0", 456) != base
    assert build_probe_cache_key("/opt/codex", "codex 1.0.0", 123) != base


def test_codex_startup_probe_pass_sets_requires_turn_id_from_golden_log(monkeypatch) -> None:
    fixture = FIXTURE_DIR / "golden-task-complete-with-turn-id.jsonl"
    monkeypatch.setenv("CCB_CODEX_TURN_ID_PROBE_LOG", str(fixture))
    monkeypatch.setenv("CCB_CODEX_TURN_ID_PROBE_BINARY", "/usr/bin/codex")
    monkeypatch.setenv("CCB_CODEX_TURN_ID_PROBE_VERSION", "codex 1.0.0")
    monkeypatch.setenv("CCB_CODEX_TURN_ID_PROBE_MTIME_NS", "123")
    runtime_state: dict[str, object] = {"request_anchor": "job_probe"}

    apply_configured_startup_turn_id_probe(runtime_state)

    assert runtime_state["requires_turn_id"] is True
    assert runtime_state["turn_id_probe_cache_key"] == build_probe_cache_key("/usr/bin/codex", "codex 1.0.0", 123)
    assert runtime_state["codex_turn_id_probe_state"] == "PASS"


def test_codex_startup_probe_fail_closes_on_turnless_golden_log(monkeypatch) -> None:
    fixture = FIXTURE_DIR / "golden-task-complete-without-turn-id.jsonl"
    monkeypatch.setenv("CCB_CODEX_TURN_ID_PROBE_LOG", str(fixture))
    runtime_state: dict[str, object] = {"request_anchor": "job_probe"}

    with pytest.raises(RuntimeError, match="startup probe failed"):
        apply_configured_startup_turn_id_probe(runtime_state)

    assert runtime_state["codex_turn_id_probe_state"] == BROKEN_STATE
    assert runtime_state["requires_rebind"] is True


def test_codex_startup_probe_default_fail_closes_when_no_installed_cli_no_log_no_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_turn_id_probe_env(monkeypatch)
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    monkeypatch.setenv("PATH", str(empty_path))
    runtime_state: dict[str, object] = {"request_anchor": "job_probe"}

    result = configured_startup_turn_id_probe()
    assert result is not None
    assert result.state == BROKEN_STATE
    assert result.turn_id is None
    assert "no installed Codex CLI found" in result.error

    with pytest.raises(RuntimeError, match="startup probe failed"):
        apply_configured_startup_turn_id_probe(runtime_state)

    assert runtime_state["requires_turn_id"] is True
    assert runtime_state["codex_turn_id_probe_state"] == BROKEN_STATE
    assert runtime_state["requires_rebind"] is True


def test_codex_startup_probe_uses_installed_cli_auto_discovery_when_no_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_turn_id_probe_env(monkeypatch)
    fake_bin = tmp_path / "bin"
    binary = _write_fake_codex(fake_bin)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ.get('PATH', '')}")

    result = configured_startup_turn_id_probe()

    assert result is not None
    assert result.state == "PASS"
    assert Path(result.binary_realpath) == binary.resolve()
    assert result.version == "codex fake 1.2.3"
    assert result.turn_id == "turn-auto-probe"


def test_codex_startup_probe_reads_installed_cli_session_log_when_stdout_has_no_turn_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_turn_id_probe_env(monkeypatch)
    fake_bin = tmp_path / "bin"
    binary = _write_fake_codex_with_session_log(fake_bin)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))

    result = configured_startup_turn_id_probe()

    assert result is not None
    assert result.state == "PASS"
    assert Path(result.binary_realpath) == binary.resolve()
    assert result.turn_id == "turn-session-probe"
    assert "thread-session-probe" in result.completion_log_path


def test_codex_startup_probe_disabled_env_override_skips_probe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_turn_id_probe_env(monkeypatch)
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    monkeypatch.setenv("PATH", str(empty_path))
    monkeypatch.setenv("CCB_CODEX_TURN_ID_PROBE_DISABLED", "1")
    runtime_state: dict[str, object] = {"request_anchor": "job_probe"}

    assert configured_startup_turn_id_probe() is None

    apply_configured_startup_turn_id_probe(runtime_state)
    assert "requires_turn_id" not in runtime_state
    assert "requires_rebind" not in runtime_state


def test_codex_startup_probe_deprecated_task_id_disabled_alias_warns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _clear_turn_id_probe_env(monkeypatch)
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    monkeypatch.setenv("PATH", str(empty_path))
    monkeypatch.setenv("CCB_CODEX_TASK_ID_PROBE_DISABLED", "1")

    assert configured_startup_turn_id_probe() is None
    assert "deprecated" in capsys.readouterr().err


def test_codex_bind_aborts_before_prompt_delivery_when_default_probe_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_turn_id_probe_env(monkeypatch)
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    monkeypatch.setenv("PATH", str(empty_path))
    sent_prompts: list[tuple[object, str, str]] = []
    monkeypatch.setattr(
        "provider_backends.codex.execution_runtime.start.send_prompt_to_runtime_target",
        lambda backend, pane_id, prompt: sent_prompts.append((backend, pane_id, prompt)),
    )
    session = SimpleNamespace(
        data={"provider": "codex"},
        session_file=tmp_path / ".ccb" / ".codex-session",
        codex_session_id="session-probe",
        ensure_pane=lambda: (True, "%7"),
    )

    with pytest.raises(RuntimeError, match="startup probe failed"):
        start_active_submission(
            SimpleNamespace(provider="codex"),
            _job(),
            context=ProviderRuntimeContext(
                agent_name="agent1",
                workspace_path=str(tmp_path),
                backend_type="tmux",
                runtime_ref=None,
                session_ref=None,
            ),
            now="2026-05-02T00:00:00Z",
            load_session_fn=lambda *_args, **_kwargs: session,
            backend_for_session_fn=lambda _data: "tmux-backend",
            reader_factory=lambda _session, _preferred_log: SimpleNamespace(
                capture_state=lambda: {"log_path": str(tmp_path / "codex-session.jsonl")}
            ),
            request_anchor_fn=lambda job_id: job_id,
            wrap_prompt_fn=lambda body, anchor: f"CCB_REQ_ID: {anchor}\n\n{body}",
        )

    assert sent_prompts == []


def test_codex_binding_rejects_turnless_completion_when_turn_id_required(monkeypatch) -> None:
    result = _poll_entries(
        monkeypatch,
        [
            {"role": "meta", "entry_type": "event_msg", "payload_type": "task_started", "turn_id": "turn-shared"},
            {"role": "meta", "entry_type": "turn_context", "payload_type": "turn_context", "turn_id": "turn-shared"},
            {"role": "user", "text": "CCB_REQ_ID: job_probe\n\nprompt"},
            {"role": "assistant", "text": "unkeyed same-turn answer"},
            {
                "role": "system",
                "entry_type": "event_msg",
                "payload_type": "task_complete",
                "last_agent_message": "unkeyed final",
            },
        ],
        runtime_state={
            "requires_turn_id": True,
            "turn_id_probe_cache_key": build_probe_cache_key("/usr/bin/codex", "codex 1.0.0", 123),
        },
    )

    assert result is not None
    assert [item.kind for item in result.items] == [CompletionItemKind.ANCHOR_SEEN]
    assert result.submission.reply == ""
    assert result.submission.runtime_state["bound_turn_contaminated"] is True


def test_codex_runtime_marks_degraded_and_requires_rebind_on_midsession_turnless_entry(monkeypatch) -> None:
    result = _poll_entries(
        monkeypatch,
        [
            {"role": "meta", "entry_type": "event_msg", "payload_type": "task_started", "turn_id": "turn-shared"},
            {"role": "meta", "entry_type": "turn_context", "payload_type": "turn_context", "turn_id": "turn-shared"},
            {"role": "user", "text": "CCB_REQ_ID: job_probe\n\nprompt"},
            {
                "role": "system",
                "entry_type": "event_msg",
                "payload_type": "task_complete",
                "last_agent_message": "turnless final",
            },
        ],
        runtime_state={
            "requires_turn_id": True,
            "turn_id_probe_cache_key": build_probe_cache_key("/usr/bin/codex", "codex 1.0.0", 123),
        },
    )

    assert result is not None
    assert result.submission.runtime_state["codex_turn_id_probe_state"] == BROKEN_STATE
    assert result.submission.runtime_state["requires_rebind"] is True
