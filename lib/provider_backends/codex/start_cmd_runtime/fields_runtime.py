from __future__ import annotations

import shlex
from typing import Mapping

from .parsing import extract_resume_session_id, find_codex_token_index, looks_like_bare_resume_cmd
from .rewriting import build_resume_start_cmd


def effective_start_cmd(data: Mapping[str, object]) -> str:
    if not isinstance(data, Mapping):
        return ""
    start_cmd = stored_start_cmd(data)
    codex_start_cmd = stored_codex_start_cmd(data)
    session_id = resolved_session_id(data, start_cmd=start_cmd, codex_start_cmd=codex_start_cmd)
    if should_rebuild_resume_command(session_id=session_id, start_cmd=start_cmd, codex_start_cmd=codex_start_cmd):
        return build_resume_start_cmd(start_cmd, session_id)
    return codex_start_cmd or start_cmd


def persist_resume_start_cmd_fields(data: dict[str, object], session_id: object) -> str | None:
    if not isinstance(data, dict):
        return None
    normalized_session_id = normalized_session_value(session_id)
    if not normalized_session_id:
        return None
    resume_start_cmd = build_resume_start_cmd(resume_template_command(data), normalized_session_id)
    data["codex_start_cmd"] = resume_start_cmd
    data["start_cmd"] = resume_start_cmd
    return resume_start_cmd


def strip_resume_start_cmd(command: object) -> str:
    raw = str(command or "").strip()
    if not raw:
        return ""
    shell_prefix, separator, tail = raw.rpartition(";")
    segment = tail.strip() if separator else raw
    stripped_segment = _strip_resume_from_segment(segment)
    if separator:
        prefix = shell_prefix.strip()
        if stripped_segment:
            return f"{prefix}; {stripped_segment}" if prefix else stripped_segment
        return prefix
    return stripped_segment


def resume_template_command(data: Mapping[str, object]) -> str:
    start_cmd = stored_start_cmd(data)
    if start_cmd and not looks_like_bare_resume_cmd(start_cmd):
        return start_cmd
    codex_start_cmd = stored_codex_start_cmd(data)
    if codex_start_cmd:
        return codex_start_cmd
    return start_cmd


def stored_start_cmd(data: Mapping[str, object]) -> str:
    return str(data.get("start_cmd") or "").strip()


def stored_codex_start_cmd(data: Mapping[str, object]) -> str:
    return str(data.get("codex_start_cmd") or "").strip()


def resolved_session_id(data: Mapping[str, object], *, start_cmd: str, codex_start_cmd: str) -> str:
    candidates = (
        data.get("codex_session_id"),
        extract_resume_session_id(codex_start_cmd),
        extract_resume_session_id(start_cmd),
    )
    for candidate in candidates:
        text = normalized_session_value(candidate)
        if text:
            return text
    return ""


def normalized_session_value(session_id: object) -> str:
    return str(session_id or "").strip()


def should_rebuild_resume_command(*, session_id: str, start_cmd: str, codex_start_cmd: str) -> bool:
    return bool(
        session_id
        and start_cmd
        and (not codex_start_cmd or looks_like_bare_resume_cmd(codex_start_cmd))
    )


def _strip_resume_from_segment(segment: str) -> str:
    try:
        tokens = shlex.split(segment)
    except Exception:
        return segment
    codex_index = find_codex_token_index(tokens)
    if codex_index is None:
        return segment
    result: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if index > codex_index and token == "resume":
            index += 2
            continue
        result.append(token)
        index += 1
    return " ".join(shlex.quote(str(token)) for token in result)


__all__ = ["effective_start_cmd", "persist_resume_start_cmd_fields", "resume_template_command", "strip_resume_start_cmd"]
