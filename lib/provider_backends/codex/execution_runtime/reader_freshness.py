from __future__ import annotations

import json
from pathlib import Path


def stash_reader_freshness(runtime_state: dict, *, session_file: Path | None, session_id: str | None) -> None:
    if session_file is None:
        runtime_state["reader_session_file"] = None
        runtime_state["reader_session_file_mtime"] = 0.0
        runtime_state["reader_session_id_at_build"] = session_id or ""
        return
    try:
        mtime = Path(session_file).stat().st_mtime
    except OSError:
        mtime = 0.0
    runtime_state["reader_session_file"] = str(session_file)
    runtime_state["reader_session_file_mtime"] = mtime
    runtime_state["reader_session_id_at_build"] = session_id or ""


def reader_is_stale(runtime_state: dict) -> bool:
    raw_session_file = runtime_state.get("reader_session_file")
    if not raw_session_file:
        return False
    try:
        session_file = Path(str(raw_session_file)).expanduser()
    except Exception:
        return False
    if not session_file.is_file():
        return False
    try:
        current_mtime = session_file.stat().st_mtime
        baseline_mtime = float(runtime_state.get("reader_session_file_mtime") or 0.0)
    except Exception:
        return False
    if current_mtime <= baseline_mtime:
        return False
    try:
        data = json.loads(session_file.read_text(encoding="utf-8-sig"))
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    current_sid = str(data.get("codex_session_id") or "").strip()
    baseline_sid = str(runtime_state.get("reader_session_id_at_build") or "").strip()
    return bool(current_sid and current_sid != baseline_sid)


__all__ = ["reader_is_stale", "stash_reader_freshness"]
