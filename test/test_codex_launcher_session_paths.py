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
