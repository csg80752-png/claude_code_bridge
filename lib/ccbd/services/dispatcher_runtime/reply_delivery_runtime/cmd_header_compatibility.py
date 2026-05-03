from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any

from project.ids import compute_project_id
from storage.paths import PathLayout

_MARKER_SCHEMA_VERSION = 1
_CYCLE = "v8.3.2"
_JOB_ID_RE = re.compile(r"^job_[0-9a-f]{8,16}$")


@dataclass(frozen=True)
class CmdHeaderOnlyCompatibilityResult:
    compatible: bool
    reason: str
    marker_path: Path | None = None


def validate_cmd_header_only_compatibility_marker(project_root: Path | None) -> CmdHeaderOnlyCompatibilityResult:
    if project_root is None:
        return _result(False, "missing_project_root", None, project_root=None)
    root = Path(project_root)
    marker_path = PathLayout(root).ccbd_cmd_header_only_compatibility_path
    if not marker_path.exists():
        return _result(False, "marker_absent", marker_path, project_root=root)
    try:
        payload = json.loads(marker_path.read_text(encoding="utf-8"))
    except PermissionError:
        return _result(False, "marker_unreadable", marker_path, project_root=root)
    except Exception:
        return _result(False, "marker_malformed", marker_path, project_root=root)
    if not isinstance(payload, dict):
        return _result(False, "marker_malformed", marker_path, project_root=root)

    expected_project_id = compute_project_id(root)
    current_commit = current_build_commit()
    current_prompt_hash = current_prompt_instruction_hash()
    checks = (
        (payload.get("schema_version") == _MARKER_SCHEMA_VERSION, "schema_version_mismatch"),
        (payload.get("cycle") == _CYCLE, "cycle_mismatch"),
        (payload.get("mode") == "header_only", "mode_mismatch"),
        (payload.get("observed_auto_pend") is True, "auto_pend_not_observed"),
        (payload.get("project_id") == expected_project_id, "project_id_mismatch"),
        (bool(current_commit) and payload.get("build_commit") == current_commit, "build_commit_mismatch"),
        (
            bool(current_prompt_hash) and payload.get("prompt_instruction_hash") == current_prompt_hash,
            "prompt_instruction_hash_mismatch",
        ),
        (_JOB_ID_RE.fullmatch(str(payload.get("smoke_job_id") or "")) is not None, "smoke_job_id_invalid"),
        (bool(str(payload.get("smoke_reply_id") or "").strip()), "smoke_reply_id_missing"),
        (bool(str(payload.get("cmd_pane_id") or "").strip()), "cmd_pane_id_missing"),
        (_parse_created_at(payload.get("created_at")) is not None, "created_at_invalid"),
    )
    for passed, reason in checks:
        if not passed:
            return _result(False, reason, marker_path, project_root=root)
    return _result(True, "marker_valid", marker_path, project_root=root)


def write_cmd_header_only_compatibility_marker(
    project_root: Path,
    *,
    smoke_job_id: str,
    smoke_reply_id: str,
    cmd_pane_id: str,
    observed_auto_pend: bool = True,
    created_at: str | None = None,
) -> Path:
    root = Path(project_root)
    marker_path = PathLayout(root).ccbd_cmd_header_only_compatibility_path
    payload = build_cmd_header_only_compatibility_marker(
        root,
        smoke_job_id=smoke_job_id,
        smoke_reply_id=smoke_reply_id,
        cmd_pane_id=cmd_pane_id,
        observed_auto_pend=observed_auto_pend,
        created_at=created_at,
    )
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = marker_path.with_name(f"{marker_path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_parent(tmp_path)
        os.replace(tmp_path, marker_path)
        _fsync_parent(marker_path)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
    return marker_path


def build_cmd_header_only_compatibility_marker(
    project_root: Path,
    *,
    smoke_job_id: str,
    smoke_reply_id: str,
    cmd_pane_id: str,
    observed_auto_pend: bool = True,
    created_at: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": _MARKER_SCHEMA_VERSION,
        "cycle": _CYCLE,
        "mode": "header_only",
        "project_id": compute_project_id(Path(project_root)),
        "build_commit": current_build_commit(),
        "prompt_instruction_hash": current_prompt_instruction_hash(),
        "smoke_job_id": str(smoke_job_id),
        "smoke_reply_id": str(smoke_reply_id),
        "cmd_pane_id": str(cmd_pane_id),
        "observed_auto_pend": bool(observed_auto_pend),
        "created_at": created_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def current_build_commit() -> str | None:
    root = _install_root()
    if (root / ".git").exists():
        try:
            result = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=2,
            )
        except Exception:
            result = None
        if result is not None and result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    build_info = root / "BUILD_INFO.json"
    try:
        payload = json.loads(build_info.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    commit = str(payload.get("commit") or "").strip()
    return commit or None


def current_prompt_instruction_hash() -> str | None:
    prompt_path = _install_root() / "config" / "claude-md-ccb.md"
    try:
        digest = hashlib.sha256(prompt_path.read_bytes()).hexdigest()
    except Exception:
        return None
    return f"sha256:{digest}"


def marker_path_for_project(project_root: Path) -> Path:
    return PathLayout(project_root).ccbd_cmd_header_only_compatibility_path


def _install_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "config" / "claude-md-ccb.md").exists() or (parent / "BUILD_INFO.json").exists():
            return parent
    return current.parents[5]


def _parse_created_at(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _result(
    compatible: bool,
    reason: str,
    marker_path: Path | None,
    *,
    project_root: Path | None,
) -> CmdHeaderOnlyCompatibilityResult:
    if project_root is not None:
        try:
            from .cmd_delivery_telemetry import record_cmd_header_only_compatibility_marker

            record_cmd_header_only_compatibility_marker(
                project_root,
                compatible=compatible,
                reason=reason,
                marker_path=str(marker_path) if marker_path is not None else None,
            )
        except Exception:
            pass
    return CmdHeaderOnlyCompatibilityResult(compatible=compatible, reason=reason, marker_path=marker_path)


def _fsync_parent(path: Path) -> None:
    try:
        fd = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


__all__ = [
    "CmdHeaderOnlyCompatibilityResult",
    "build_cmd_header_only_compatibility_marker",
    "current_build_commit",
    "current_prompt_instruction_hash",
    "marker_path_for_project",
    "validate_cmd_header_only_compatibility_marker",
    "write_cmd_header_only_compatibility_marker",
]
