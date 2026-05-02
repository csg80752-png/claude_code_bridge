from __future__ import annotations

from pathlib import Path

import pytest

from ccbd.services.dispatcher_runtime.reply_delivery_runtime.cmd_readiness_probes import claude_ready
from provider_backends.codex.execution_runtime.state_machine_runtime.models import CodexPollState
from provider_backends.codex.execution_runtime.state_machine_runtime.serialization import to_runtime_state
from provider_backends.codex.launcher_runtime.task_id_probe import (
    BROKEN_STATE,
    apply_configured_startup_task_id_probe,
    probe_from_completion_log,
)


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "codex_task_id_probe"


def test_b5_cmd_readiness_canary_accepts_ready_prompt_and_rejects_modal_wrap() -> None:
    assert claude_ready("❯ ") is True
    assert claude_ready("Do you want to allow this?\ncontext\nwrap\nlines\n❯ ") is False


def test_b5_codex_identity_canary_requires_task_id_probe_artifact() -> None:
    result = probe_from_completion_log(
        FIXTURE_DIR / "golden-task-complete-with-task-id.jsonl",
        binary_realpath="/usr/bin/codex",
        version="codex 1.0.0",
        binary_mtime_ns=123,
    )

    assert result.state == "PASS"
    assert result.task_id == "task_probe_fixture"


def test_b5_default_codex_bind_fails_closed_without_probe_artifact(monkeypatch, tmp_path: Path) -> None:
    for key in (
        "CCB_CODEX_TASK_ID_PROBE_LOG",
        "CCB_CODEX_TASK_ID_PROBE_BINARY",
        "CCB_CODEX_TASK_ID_PROBE_VERSION",
        "CCB_CODEX_TASK_ID_PROBE_MTIME_NS",
        "CCB_CODEX_TASK_ID_PROBE_REQUIRED",
        "CCB_CODEX_TASK_ID_PROBE_DISABLED",
    ):
        monkeypatch.delenv(key, raising=False)
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    monkeypatch.setenv("PATH", str(empty_path))
    runtime_state: dict[str, object] = {"request_anchor": "job_b5"}

    with pytest.raises(RuntimeError, match="startup probe failed"):
        apply_configured_startup_task_id_probe(runtime_state)

    assert runtime_state["requires_task_id"] is True
    assert runtime_state["codex_task_id_probe_state"] == BROKEN_STATE
    assert runtime_state["requires_rebind"] is True


def test_b5_codex_poll_state_canary_exports_required_identity_flag() -> None:
    state = to_runtime_state(
        CodexPollState(
            request_anchor="job_b5",
            bound_task_id="task-b5",
            requires_task_id=True,
            task_id_probe_cache_key="codex:/usr/bin/codex:1:123",
        )
    )

    assert state["requires_task_id"] is True
    assert state["task_id_probe_cache_key"] == "codex:/usr/bin/codex:1:123"


def test_b5_agent3_isolation_canary_keeps_claude_provider_configured() -> None:
    config = (Path(__file__).resolve().parents[1] / ".ccb" / "ccb.config").read_text(encoding="utf-8")

    assert "agent3:claude" in config
