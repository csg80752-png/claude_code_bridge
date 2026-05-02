from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

from provider_backends.codex.comm import CodexLogReader


def test_codex_log_reader_keeps_bound_session(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    work_dir = tmp_path / "repo"
    work_dir.mkdir()
    preferred = root / "2026" / "abc-session.jsonl"
    newer = root / "2026" / "other-session.jsonl"
    preferred.parent.mkdir(parents=True)

    meta = json.dumps({"type": "session_meta", "payload": {"cwd": str(work_dir)}}) + "\n"
    preferred.write_text(meta, encoding="utf-8")
    preferred_mtime = preferred.stat().st_mtime
    os.utime(preferred, (preferred_mtime - 30.0, preferred_mtime - 30.0))
    newer.write_text(meta, encoding="utf-8")
    preferred_mtime = preferred.stat().st_mtime
    newer_mtime = newer.stat().st_mtime
    os.utime(preferred, (preferred_mtime - 30.0, preferred_mtime - 30.0))
    os.utime(newer, (newer_mtime, newer_mtime))

    reader = CodexLogReader(
        root=root,
        log_path=preferred,
        session_id_filter="abc",
        work_dir=work_dir,
    )

    assert reader.current_log_path() == preferred


def test_codex_log_reader_follows_newer_workspace_session_when_enabled(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    work_dir = tmp_path / "repo"
    work_dir.mkdir()
    preferred = root / "2026" / "abc-session.jsonl"
    preferred.parent.mkdir(parents=True, exist_ok=True)

    meta = json.dumps({"type": "session_meta", "payload": {"cwd": str(work_dir)}}) + "\n"
    preferred.write_text(meta, encoding="utf-8")

    reader = CodexLogReader(
        root=root,
        log_path=preferred,
        session_id_filter="abc",
        work_dir=work_dir,
        follow_workspace_sessions=True,
    )
    state = reader.capture_state()
    assert state["log_path"] == preferred

    rotated = root / "2026" / "rotated-session.jsonl"
    rotated.write_text(
        meta
        + json.dumps(
            {
                "timestamp": "2026-04-04T10:39:14.000Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "CCB_REQ_ID: req-rotate\n\nhello"}],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rotated_mtime = rotated.stat().st_mtime
    os.utime(rotated, (rotated_mtime + 30.0, rotated_mtime + 30.0))
    state["last_rescan"] = 0.0

    entries, next_state = reader.try_get_entries(state)

    assert entries == []
    assert next_state["log_path"] == rotated
    assert reader.current_log_path() == rotated

    entries, _final_state = reader.try_get_entries(next_state)
    assert len(entries) == 1
    assert entries[0]["role"] == "user"
    assert "CCB_REQ_ID: req-rotate" in entries[0]["text"]


def test_codex_isolated_reader_rejects_preferred_log_outside_root(tmp_path: Path) -> None:
    isolated_root = tmp_path / "isolated-sessions"
    legacy_root = tmp_path / "legacy-sessions"
    work_dir = tmp_path / "repo"
    work_dir.mkdir()
    legacy_log = legacy_root / "2026" / "legacy-session.jsonl"
    legacy_log.parent.mkdir(parents=True, exist_ok=True)
    legacy_log.write_text(
        json.dumps({"type": "session_meta", "payload": {"cwd": str(work_dir)}}) + "\n",
        encoding="utf-8",
    )

    reader = CodexLogReader(
        root=isolated_root,
        log_path=legacy_log,
        session_id_filter="legacy",
        work_dir=work_dir,
        isolated_to_root=True,
    )

    assert reader.current_log_path() is None


def test_codex_polling_ensure_log_rejects_preferred_log_outside_isolated_root(tmp_path: Path) -> None:
    from provider_backends.codex.comm_runtime.polling_runtime.logs import ensure_log

    isolated_root = tmp_path / "isolated-sessions"
    legacy_root = tmp_path / "legacy-sessions"
    work_dir = tmp_path / "repo"
    work_dir.mkdir()
    legacy_log = legacy_root / "2026" / "legacy-session.jsonl"
    legacy_log.parent.mkdir(parents=True, exist_ok=True)
    legacy_log.write_text(
        json.dumps({"type": "session_meta", "payload": {"cwd": str(work_dir)}}) + "\n",
        encoding="utf-8",
    )

    reader = CodexLogReader(
        root=isolated_root,
        log_path=legacy_log,
        session_id_filter="legacy",
        work_dir=work_dir,
        isolated_to_root=True,
    )

    try:
        ensure_log(reader, None)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("isolated polling must not read the legacy preferred log")
    assert reader.current_log_path() is None


def test_codex_log_reader_replays_first_entries_when_log_appears_after_capture(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    work_dir = tmp_path / "repo"
    work_dir.mkdir()

    reader = CodexLogReader(root=root, work_dir=work_dir)
    state = reader.capture_state()
    assert state["log_path"] is None
    assert state["offset"] == -1

    log_path = root / "2026" / "ccb-codex-session.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "\n".join(
            [
                json.dumps({"type": "session_meta", "payload": {"cwd": str(work_dir)}}),
                json.dumps(
                    {
                        "timestamp": "2026-03-24T00:00:00.000Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "CCB_REQ_ID: req-1\n\nhello"}],
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    entries, next_state = reader.try_get_entries(state)

    assert len(entries) == 1
    assert entries[0]["role"] == "user"
    assert "CCB_REQ_ID: req-1" in entries[0]["text"]
    assert next_state["log_path"] == log_path


def test_codex_execution_reader_factory_enables_workspace_follow(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from provider_execution import codex as codex_adapter_module

    captured: dict[str, object] = {}

    class _Reader:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    class _Session:
        codex_session_path = str(tmp_path / "session.jsonl")
        codex_session_id = "session-old"
        work_dir = str(tmp_path / "repo")

    monkeypatch.setenv("CCB_CODEX_FOLLOW_WORKSPACE", "1")
    monkeypatch.setattr(codex_adapter_module, "CodexLogReader", _Reader)

    codex_adapter_module._reader_factory(_Session(), None)

    assert captured["log_path"] == tmp_path / "session.jsonl"
    assert captured["session_id_filter"] == "session-old"
    assert captured["work_dir"] == tmp_path / "repo"
    assert captured["follow_workspace_sessions"] is True


def test_claude_execution_reader_factory_uses_session_projects_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from provider_execution import claude as claude_adapter_module

    captured: dict[str, object] = {}
    projects_root = tmp_path / "runtime" / "claude-home" / ".claude" / "projects"

    class _Reader:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    class _Session:
        work_dir = str(tmp_path / "repo")
        data = {"claude_projects_root": str(projects_root)}

    monkeypatch.setattr(claude_adapter_module, "ClaudeLogReader", _Reader)

    claude_adapter_module._reader_factory(_Session())

    assert captured["root"] == projects_root
    assert captured["work_dir"] == tmp_path / "repo"


def test_claude_execution_reader_factory_derives_isolated_root_for_legacy_session(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from provider_execution import claude as claude_adapter_module

    captured: dict[str, object] = {}
    work_dir = tmp_path / "repo"
    work_dir.mkdir()

    class _Reader:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(claude_adapter_module, "ClaudeLogReader", _Reader)

    claude_adapter_module._reader_factory(SimpleNamespace(work_dir=str(work_dir), data={}))

    assert captured["root"] == work_dir / ".ccb" / "claude-home" / ".claude" / "projects"
    assert captured["root"] != Path.home() / ".claude" / "projects"
    assert captured["work_dir"] == work_dir


def test_codex_export_runtime_state_preserves_turn_binding_fields() -> None:
    from completion.models import CompletionSourceKind
    from provider_execution.base import ProviderSubmission
    from provider_execution.codex import CodexProviderAdapter

    submission = ProviderSubmission(
        job_id="job_1",
        agent_name="agent2",
        provider="codex",
        accepted_at="2026-04-24T00:00:00Z",
        ready_at="2026-04-24T00:00:00Z",
        source_kind=CompletionSourceKind.PROTOCOL_EVENT_STREAM,
        reply="",
        runtime_state={
            "current_turn_id": "turn-shared",
            "current_task_id": "task-current",
            "current_turn_started": True,
            "bound_turn_started": True,
            "bound_turn_contaminated": True,
        },
    )

    state = CodexProviderAdapter().export_runtime_state(submission)

    assert state["current_turn_id"] == "turn-shared"
    assert state["current_task_id"] == "task-current"
    assert state["current_turn_started"] is True
    assert state["bound_turn_started"] is True
    assert state["bound_turn_contaminated"] is True


def test_snapshot_readiness_and_preparation_modules_import_from_pre_v82_snapshot() -> None:
    snapshot = Path("/home/speed/.local/share/codex-dual.snapshot-pre-v8.2-20260501-011038")
    modules = (
        "ccbd.services.dispatcher_runtime.reply_delivery_runtime.cmd_readiness_probes",
        "ccbd.services.dispatcher_runtime.reply_delivery_runtime.preparation_service",
    )
    for module_name in modules:
        module_path = snapshot / "lib" / Path(*module_name.split(".")).with_suffix(".py")
        assert module_path.exists(), module_path

    completed = subprocess.run(
        [
            "python3",
            "-c",
            "import ccbd.services.dispatcher_runtime.reply_delivery_runtime.cmd_readiness_probes; "
            "import ccbd.services.dispatcher_runtime.reply_delivery_runtime.preparation_service",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(snapshot / "lib")},
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_codex_log_reader_matches_wsl_and_windows_style_workdirs(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    log_path = root / "2026" / "wsl-session.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        json.dumps({"type": "session_meta", "payload": {"cwd": "/mnt/C/Users/alice/repo"}}) + "\n",
        encoding="utf-8",
    )

    reader = CodexLogReader(root=root, work_dir=Path("c:/Users/alice/repo"))

    assert reader.current_log_path() == log_path


def test_resolve_unique_codex_session_target_skips_ambiguous_instances(tmp_path: Path) -> None:
    from provider_backends.codex.comm import _resolve_unique_codex_session_target

    work_dir = tmp_path / "repo"
    config_dir = work_dir / ".ccb"
    config_dir.mkdir(parents=True)
    (config_dir / ".codex-auth-session").write_text("{}", encoding="utf-8")
    (config_dir / ".codex-payment-session").write_text("{}", encoding="utf-8")

    session_file, instance = _resolve_unique_codex_session_target(work_dir)

    assert session_file is None
    assert instance is None


def test_resolve_unique_codex_session_target_accepts_single_instance(tmp_path: Path) -> None:
    from provider_backends.codex.comm import _resolve_unique_codex_session_target

    work_dir = tmp_path / "repo"
    config_dir = work_dir / ".ccb"
    config_dir.mkdir(parents=True)
    target = config_dir / ".codex-auth-session"
    target.write_text("{}", encoding="utf-8")

    session_file, instance = _resolve_unique_codex_session_target(work_dir)

    assert session_file == target
    assert instance == "auth"
