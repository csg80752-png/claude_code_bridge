from __future__ import annotations

import json
from pathlib import Path

from provider_core.protocol import REQ_ID_PREFIX
from provider_backends.codex.comm_runtime.log_entries import extract_entry


def build_rebuild_runtime_state(submission, reader) -> dict[str, object]:
    state = dict(submission.runtime_state)
    state["reader"] = reader
    state["state"] = build_rebuild_cursor_state(submission, reader)
    reset_poll_binding_fields_for_rebuild(state)
    return state


def build_rebuild_cursor_state(submission, reader) -> dict[str, object]:
    log_path = getattr(reader, "_preferred_log", None)
    if not isinstance(log_path, Path):
        return {"log_path": None, "offset": 0}
    if not log_path.exists():
        return {"log_path": log_path, "offset": 0}

    no_wrap = bool(submission.runtime_state.get("no_wrap", False))
    request_anchor = str(submission.runtime_state.get("request_anchor") or "").strip()
    if no_wrap or not request_anchor:
        return {"log_path": log_path, "offset": eof_offset(log_path)}

    anchor_offset = find_request_anchor_offset(log_path, request_anchor)
    if anchor_offset is None:
        return {"log_path": log_path, "offset": eof_offset(log_path)}
    return {"log_path": log_path, "offset": anchor_offset}


def find_request_anchor_offset(log_path: Path, request_anchor: str) -> int | None:
    needle = f"{REQ_ID_PREFIX} {request_anchor}"
    try:
        with Path(log_path).open("rb") as handle:
            while True:
                offset = handle.tell()
                raw = handle.readline()
                if not raw:
                    return None
                entry = normalized_entry(raw)
                if not entry:
                    continue
                if str(entry.get("role") or "").lower() != "user":
                    continue
                if needle in str(entry.get("text") or ""):
                    return offset
    except OSError:
        return None


def normalized_entry(raw: bytes) -> dict[str, object] | None:
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    entry = extract_entry(data)
    return entry if isinstance(entry, dict) else None


def eof_offset(log_path: Path) -> int:
    try:
        return Path(log_path).stat().st_size
    except OSError:
        return 0


def reset_poll_binding_fields_for_rebuild(state: dict[str, object]) -> None:
    no_wrap = bool(state.get("no_wrap", False))
    state["anchor_seen"] = no_wrap
    state["bound_turn_id"] = ""
    state["bound_task_id"] = ""
    state["current_turn_id"] = ""
    state["current_task_id"] = ""
    state["current_turn_started"] = False
    state["bound_turn_started"] = False
    state["reply_buffer"] = ""
    state["last_agent_message"] = ""
    state["last_final_answer"] = ""
    state["last_assistant_message"] = ""
    state["last_assistant_signature"] = ""


__all__ = [
    "build_rebuild_cursor_state",
    "build_rebuild_runtime_state",
    "eof_offset",
    "find_request_anchor_offset",
    "normalized_entry",
    "reset_poll_binding_fields_for_rebuild",
]
