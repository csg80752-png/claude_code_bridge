from __future__ import annotations

import json
from pathlib import Path

from provider_core.pathing import session_filename_for_agent
from storage.atomic import atomic_write_json

from ..start_cmd import extract_resume_session_id
from ..start_cmd_runtime.fields_runtime import strip_resume_start_cmd
from .codex_namespace_isolation import resolve_codex_sessions_root


def load_resume_session_id(spec, runtime_dir: Path) -> str | None:
    session_path = preferred_session_path(spec, runtime_dir)
    if session_path is None:
        return None
    data = read_session_payload(session_path)
    if data is None:
        return None
    session_id = payload_resume_session_id(data)
    if not session_id:
        return None
    if _resume_id_is_resumable(session_id, runtime_dir=runtime_dir, data=data):
        return session_id
    clear_stale_session_pointer(session_path, data=data)
    return None


def agent_session_path(spec, runtime_dir: Path) -> Path | None:
    ccb_dir = find_project_ccb_dir(runtime_dir)
    if ccb_dir is None:
        return None
    return ccb_dir / session_filename_for_agent('codex', spec.name)


def find_project_ccb_dir(runtime_dir: Path) -> Path | None:
    current = Path(runtime_dir)
    for parent in (current, *current.parents):
        if parent.name == '.ccb':
            return parent
    return None


def session_file_for_runtime_dir(runtime_dir: Path) -> Path | None:
    ccb_dir = find_project_ccb_dir(runtime_dir)
    if ccb_dir is None:
        return None
    try:
        agent_name = runtime_dir.parents[1].name
    except Exception:
        return None
    agent_name = str(agent_name or '').strip()
    if not agent_name:
        return None
    return ccb_dir / session_filename_for_agent('codex', agent_name)


def preferred_session_path(spec, runtime_dir: Path) -> Path | None:
    candidates = (agent_session_path(spec, runtime_dir),)
    for session_path in candidates:
        if session_path is not None and session_path.is_file():
            return session_path
    return None


def read_session_payload(session_path: Path) -> dict | None:
    try:
        data = json.loads(session_path.read_text(encoding='utf-8'))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def payload_resume_session_id(data: dict) -> str | None:
    session_id = str(data.get('codex_session_id') or '').strip()
    if session_id:
        return session_id
    start_cmd = str(data.get('codex_start_cmd') or data.get('start_cmd') or '').strip()
    if not start_cmd:
        return None
    return extract_resume_session_id(start_cmd)


def _resume_id_is_resumable(session_id: str, *, runtime_dir: Path, data: dict) -> bool:
    root = _resolved_sessions_root(runtime_dir)
    if _path_contains_session_id(data.get("codex_session_path"), session_id, root=root):
        return True
    if not root.exists():
        return False
    try:
        for path in root.glob("**/*.jsonl"):
            if not path.is_file():
                continue
            if session_id.lower() in path.name.lower():
                return True
    except OSError:
        return False
    return False


def _resolved_sessions_root(runtime_dir: Path) -> Path:
    try:
        from provider_profiles.materializer import load_resolved_provider_profile

        return resolve_codex_sessions_root(
            runtime_dir,
            profile=load_resolved_provider_profile(runtime_dir),
        ).path
    except Exception:
        return resolve_codex_sessions_root(runtime_dir, profile=None).path


def _path_contains_session_id(value: object, session_id: str, *, root: Path) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    try:
        path = Path(raw).expanduser()
    except Exception:
        return False
    if not path.is_file():
        return False
    try:
        path.relative_to(root)
    except Exception:
        try:
            path.resolve().relative_to(root.resolve())
        except Exception:
            return False
    return session_id.lower() in path.name.lower()


def clear_stale_session_pointer(session_path: Path, *, data: dict | None = None) -> None:
    payload = dict(data if isinstance(data, dict) else (read_session_payload(session_path) or {}))
    for key in ("codex_session_id", "codex_session_path"):
        payload.pop(key, None)
    for key in ("start_cmd", "codex_start_cmd"):
        stripped = strip_resume_start_cmd(payload.get(key))
        if stripped:
            payload[key] = stripped
        else:
            payload.pop(key, None)
    atomic_write_json(session_path, payload)


__all__ = ['clear_stale_session_pointer', 'load_resume_session_id', 'payload_resume_session_id', 'session_file_for_runtime_dir']
