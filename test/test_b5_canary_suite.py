from __future__ import annotations

from pathlib import Path
import os

import pytest

from ccbd.services.dispatcher_runtime.reply_delivery_runtime.cmd_readiness_probes import claude_ready
from ccbd.services.dispatcher_runtime.reply_delivery_runtime.cmd_transport_planner import (
    CMD_HEADER_RE,
    prepare_cmd_payload,
)
from cli.management_runtime.versioning_runtime.local import get_version_info
from provider_backends.codex.execution_runtime.state_machine_runtime.models import CodexPollState
from provider_backends.codex.execution_runtime.state_machine_runtime.serialization import to_runtime_state
from provider_backends.codex.launcher_runtime.turn_id_probe import (
    BROKEN_STATE,
    apply_configured_startup_turn_id_probe,
    configured_startup_turn_id_probe,
    probe_from_completion_log,
)
from provider_backends.codex.launcher_runtime.codex_namespace_isolation import prepare_codex_home_overrides


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "codex_turn_id_probe"


def test_b5_cmd_readiness_canary_accepts_ready_prompt_and_rejects_modal_wrap() -> None:
    assert claude_ready("❯ ") is True
    assert claude_ready("Do you want to allow this?\ncontext\nwrap\nlines\n❯ ") is False


def test_b5_codex_identity_canary_requires_turn_id_probe_artifact() -> None:
    result = probe_from_completion_log(
        FIXTURE_DIR / "golden-task-complete-with-turn-id.jsonl",
        binary_realpath="/usr/bin/codex",
        version="codex 1.0.0",
        binary_mtime_ns=123,
    )

    assert result.state == "PASS"
    assert result.turn_id == "turn_probe_fixture"


def test_b5_codex_turn_id_probe_succeeds_against_installed_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "CCB_CODEX_TURN_ID_PROBE_LOG",
        "CCB_CODEX_TURN_ID_PROBE_BINARY",
        "CCB_CODEX_TURN_ID_PROBE_VERSION",
        "CCB_CODEX_TURN_ID_PROBE_MTIME_NS",
        "CCB_CODEX_TURN_ID_PROBE_DISABLED",
        "CCB_CODEX_TASK_ID_PROBE_DISABLED",
    ):
        monkeypatch.delenv(key, raising=False)

    result = configured_startup_turn_id_probe()

    assert result is not None
    assert result.state == "PASS", result
    assert result.turn_id
    install_prefix = Path(os.environ.get("CODEX_INSTALL_PREFIX") or Path.home() / ".local/share/codex-dual").expanduser()
    build_info = get_version_info(install_prefix)
    assert build_info.get("codex_cli_version")
    assert result.version == build_info["codex_cli_version"]


def test_b5_default_codex_bind_fails_closed_without_probe_artifact(monkeypatch, tmp_path: Path) -> None:
    for key in (
        "CCB_CODEX_TURN_ID_PROBE_LOG",
        "CCB_CODEX_TURN_ID_PROBE_BINARY",
        "CCB_CODEX_TURN_ID_PROBE_VERSION",
        "CCB_CODEX_TURN_ID_PROBE_MTIME_NS",
        "CCB_CODEX_TURN_ID_PROBE_DISABLED",
        "CCB_CODEX_TASK_ID_PROBE_DISABLED",
    ):
        monkeypatch.delenv(key, raising=False)
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    monkeypatch.setenv("PATH", str(empty_path))
    runtime_state: dict[str, object] = {"request_anchor": "job_b5"}

    with pytest.raises(RuntimeError, match="startup probe failed"):
        apply_configured_startup_turn_id_probe(runtime_state)

    assert runtime_state["requires_turn_id"] is True
    assert runtime_state["codex_turn_id_probe_state"] == BROKEN_STATE
    assert runtime_state["requires_rebind"] is True


def test_b5_codex_poll_state_canary_exports_required_identity_flag() -> None:
    state = to_runtime_state(
        CodexPollState(
            request_anchor="job_b5",
            bound_turn_id="turn-b5",
            requires_turn_id=True,
            turn_id_probe_cache_key="codex:/usr/bin/codex:1:123",
        )
    )

    assert state["requires_turn_id"] is True
    assert state["turn_id_probe_cache_key"] == "codex:/usr/bin/codex:1:123"


def test_b5_codex_home_isolation_two_bindings_no_state_collision(tmp_path: Path, monkeypatch) -> None:
    global_home = tmp_path / "global-codex-home"
    (global_home / "sessions").mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(global_home))
    runtime_a = tmp_path / "repo-a" / ".ccb" / "agents" / "agent1" / "provider-runtime" / "codex"
    runtime_b = tmp_path / "repo-b" / ".ccb" / "agents" / "agent1" / "provider-runtime" / "codex"

    env_a = prepare_codex_home_overrides(runtime_a, profile=None)
    env_b = prepare_codex_home_overrides(runtime_b, profile=None)

    assert env_a["CODEX_HOME"] != env_b["CODEX_HOME"]
    assert env_a["CODEX_SESSION_ROOT"] != env_b["CODEX_SESSION_ROOT"]
    assert env_a["CODEX_HOME"] != str(global_home)
    assert env_b["CODEX_HOME"] != str(global_home)
    assert Path(env_a["CODEX_SESSION_ROOT"]).is_dir()
    assert Path(env_b["CODEX_SESSION_ROOT"]).is_dir()


def test_b5_agent3_isolation_canary_keeps_claude_provider_configured() -> None:
    config = (Path(__file__).resolve().parents[1] / ".ccb" / "ccb.config").read_text(encoding="utf-8")

    assert "agent3:claude" in config


def test_b5_header_only_cmd_delivery_canary_multi_consumer_payload_safe() -> None:
    header = prepare_cmd_payload(sender_id="agent1", body_bytes=17, source_job_id="job_1234abcd")

    assert CMD_HEADER_RE.fullmatch(header)
    assert len(header) <= 100
    assert "\n" not in header
    assert all(token not in header for token in ("`", "$", ";", "|"))
