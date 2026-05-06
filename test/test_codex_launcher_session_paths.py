from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from provider_backends.codex.launcher_runtime.session_paths import load_resume_session_id


def test_load_resume_session_id_prefers_session_field_then_start_cmd(tmp_path: Path) -> None:
    ccb_dir = tmp_path / ".ccb"
    agent_dir = ccb_dir / "agents" / "agent1" / "runtime"
    agent_dir.mkdir(parents=True, exist_ok=True)
    session_file = ccb_dir / ".codex-agent1-session"
    session_file.write_text(json.dumps({"codex_session_id": "sid-1"}), encoding="utf-8")
    sessions_root = agent_dir / "codex-home" / "sessions"
    sessions_root.mkdir(parents=True, exist_ok=True)
    (sessions_root / "rollout-sid-1.jsonl").write_text("", encoding="utf-8")

    spec = SimpleNamespace(name="agent1")

    assert load_resume_session_id(spec, agent_dir) == "sid-1"

    session_file.write_text(json.dumps({"start_cmd": "codex resume sid-2"}), encoding="utf-8")
    (sessions_root / "rollout-sid-2.jsonl").write_text("", encoding="utf-8")

    assert load_resume_session_id(spec, agent_dir) == "sid-2"


def test_load_resume_session_id_respects_session_payload_codex_session_root(tmp_path: Path) -> None:
    ccb_dir = tmp_path / ".ccb"
    runtime_dir = ccb_dir / "agents" / "agent1" / "provider-runtime" / "codex"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    explicit_root = tmp_path / "operator-home" / "sessions"
    explicit_root.mkdir(parents=True)
    (explicit_root / "rollout-explicit-root-session.jsonl").write_text("", encoding="utf-8")
    session_file = ccb_dir / ".codex-agent1-session"
    session_file.write_text(
        json.dumps(
            {
                "codex_session_id": "explicit-root-session",
                "codex_home": str(tmp_path / "operator-home"),
                "codex_session_root": str(explicit_root),
            }
        ),
        encoding="utf-8",
    )
    isolated_root = runtime_dir / "codex-home" / "sessions"
    isolated_root.mkdir(parents=True)

    spec = SimpleNamespace(name="agent1")

    assert load_resume_session_id(spec, runtime_dir) == "explicit-root-session"


def test_load_resume_session_id_rejects_metadata_only_codex_session_path(tmp_path: Path) -> None:
    ccb_dir = tmp_path / ".ccb"
    runtime_dir = ccb_dir / "agents" / "agent2" / "provider-runtime" / "codex"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    sessions_root = runtime_dir / "codex-home" / "sessions" / "2026" / "05" / "07"
    sessions_root.mkdir(parents=True)
    session_id = "019dfe6a-df2c-7f10-a0ae-e5dff9f47b3b"
    metadata_only_log = sessions_root / f"rollout-2026-05-07T02-51-50-{session_id}.jsonl"
    metadata_only_log.write_text(
        json.dumps(
            {
                "timestamp": "2026-05-06T17:51:50.122Z",
                "type": "session_meta",
                "payload": {"id": session_id, "cwd": str(tmp_path)},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    session_file = ccb_dir / ".codex-agent2-session"
    session_file.write_text(
        json.dumps(
            {
                "codex_session_id": session_id,
                "codex_session_path": str(metadata_only_log),
                "codex_start_cmd": f"codex -c disable_paste_burst=true resume {session_id}",
            }
        ),
        encoding="utf-8",
    )

    spec = SimpleNamespace(name="agent2")

    assert load_resume_session_id(spec, runtime_dir) is None
    updated = json.loads(session_file.read_text(encoding="utf-8"))
    assert "codex_session_id" not in updated
    assert "codex_session_path" not in updated
    assert "resume" not in updated.get("codex_start_cmd", "")


def test_load_resume_session_id_rejects_long_metadata_only_codex_session_path(tmp_path: Path) -> None:
    ccb_dir = tmp_path / ".ccb"
    runtime_dir = ccb_dir / "agents" / "agent2" / "provider-runtime" / "codex"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    sessions_root = runtime_dir / "codex-home" / "sessions"
    sessions_root.mkdir(parents=True)
    session_id = "019dfe6a-df2c-7f10-a0ae-e5dff9f47b3b"
    metadata_only_log = sessions_root / f"rollout-{session_id}.jsonl"
    metadata_only_log.write_text(
        "".join(
            json.dumps({"type": "session_meta", "payload": {"id": session_id, "index": index}}) + "\n"
            for index in range(80)
        ),
        encoding="utf-8",
    )
    session_file = ccb_dir / ".codex-agent2-session"
    session_file.write_text(
        json.dumps({"codex_session_id": session_id, "codex_session_path": str(metadata_only_log)}),
        encoding="utf-8",
    )

    spec = SimpleNamespace(name="agent2")

    assert load_resume_session_id(spec, runtime_dir) is None


def test_load_resume_session_id_accepts_codex_session_with_user_content(tmp_path: Path) -> None:
    ccb_dir = tmp_path / ".ccb"
    runtime_dir = ccb_dir / "agents" / "agent2" / "provider-runtime" / "codex"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    sessions_root = runtime_dir / "codex-home" / "sessions" / "2026" / "05" / "07"
    sessions_root.mkdir(parents=True)
    session_id = "019dfe6a-df2c-7f10-a0ae-e5dff9f47b3b"
    session_log = sessions_root / f"rollout-2026-05-07T02-51-50-{session_id}.jsonl"
    session_log.write_text(
        json.dumps({"type": "session_meta", "payload": {"id": session_id}})
        + "\n"
        + json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "continue"}})
        + "\n",
        encoding="utf-8",
    )
    session_file = ccb_dir / ".codex-agent2-session"
    session_file.write_text(
        json.dumps(
            {
                "codex_session_id": session_id,
                "codex_session_path": str(session_log),
                "codex_start_cmd": f"codex -c disable_paste_burst=true resume {session_id}",
            }
        ),
        encoding="utf-8",
    )

    spec = SimpleNamespace(name="agent2")

    assert load_resume_session_id(spec, runtime_dir) == session_id
