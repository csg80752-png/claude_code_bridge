from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any

CODEX_TASK_ID_PROBE_SCHEMA_VERSION = 1
PROBE_TIMEOUT_SECONDS = 10
BROKEN_STATE = "BROKEN"


@dataclass(frozen=True)
class CodexTaskIdProbeResult:
    schema_version: int
    state: str
    binary_realpath: str
    version: str
    binary_mtime_ns: int
    completion_log_path: str
    task_id: str | None
    probe_timeout_seconds: int
    error: str = ""

    @classmethod
    def pass_result(
        cls,
        *,
        binary_realpath: str,
        version: str,
        binary_mtime_ns: int,
        completion_log_path: str,
        task_id: str,
        probe_timeout_seconds: int = PROBE_TIMEOUT_SECONDS,
    ) -> "CodexTaskIdProbeResult":
        return cls(
            schema_version=CODEX_TASK_ID_PROBE_SCHEMA_VERSION,
            state="PASS",
            binary_realpath=str(binary_realpath),
            version=str(version),
            binary_mtime_ns=int(binary_mtime_ns),
            completion_log_path=str(completion_log_path),
            task_id=str(task_id),
            probe_timeout_seconds=int(probe_timeout_seconds),
        )

    @classmethod
    def broken_result(
        cls,
        *,
        binary_realpath: str,
        version: str,
        binary_mtime_ns: int,
        completion_log_path: str,
        error: str,
        probe_timeout_seconds: int = PROBE_TIMEOUT_SECONDS,
    ) -> "CodexTaskIdProbeResult":
        return cls(
            schema_version=CODEX_TASK_ID_PROBE_SCHEMA_VERSION,
            state=BROKEN_STATE,
            binary_realpath=str(binary_realpath),
            version=str(version),
            binary_mtime_ns=int(binary_mtime_ns),
            completion_log_path=str(completion_log_path),
            task_id=None,
            probe_timeout_seconds=int(probe_timeout_seconds),
            error=str(error),
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "record_type": "codex_task_id_probe_result",
            "state": self.state,
            "binary_realpath": self.binary_realpath,
            "version": self.version,
            "binary_mtime_ns": self.binary_mtime_ns,
            "completion_log_path": self.completion_log_path,
            "task_id": self.task_id,
            "probe_timeout_seconds": self.probe_timeout_seconds,
            "error": self.error,
            "cache_key": build_probe_cache_key(self.binary_realpath, self.version, self.binary_mtime_ns),
        }


def build_probe_cache_key(binary_realpath: str, version: str, binary_mtime_ns: int) -> str:
    return f"{Path(binary_realpath).as_posix()}::{version}::{int(binary_mtime_ns)}"


def probe_from_completion_log(
    completion_log_path: str | Path,
    *,
    binary_realpath: str,
    version: str,
    binary_mtime_ns: int,
    probe_timeout_seconds: int = PROBE_TIMEOUT_SECONDS,
) -> CodexTaskIdProbeResult:
    path = Path(completion_log_path)
    task_id = _first_completion_task_id(path)
    if task_id:
        return CodexTaskIdProbeResult.pass_result(
            binary_realpath=binary_realpath,
            version=version,
            binary_mtime_ns=binary_mtime_ns,
            completion_log_path=str(path),
            task_id=task_id,
            probe_timeout_seconds=probe_timeout_seconds,
        )
    return CodexTaskIdProbeResult.broken_result(
        binary_realpath=binary_realpath,
        version=version,
        binary_mtime_ns=binary_mtime_ns,
        completion_log_path=str(path),
        error="task_id missing from task_complete entry",
        probe_timeout_seconds=probe_timeout_seconds,
    )


def apply_configured_startup_task_id_probe(runtime_state: dict[str, object]) -> None:
    result = configured_startup_task_id_probe()
    if result is None:
        return
    if result.state == BROKEN_STATE:
        runtime_state["codex_task_id_probe_state"] = BROKEN_STATE
        runtime_state["requires_rebind"] = True
        raise RuntimeError(f"Codex task_id startup probe failed: {result.error}")
    runtime_state["requires_task_id"] = True
    runtime_state["task_id_probe_cache_key"] = build_probe_cache_key(
        result.binary_realpath,
        result.version,
        result.binary_mtime_ns,
    )
    runtime_state["codex_task_id_probe_state"] = result.state
    runtime_state["codex_task_id_probe_artifact"] = result.to_record()


def configured_startup_task_id_probe() -> CodexTaskIdProbeResult | None:
    log_path = os.environ.get("CCB_CODEX_TASK_ID_PROBE_LOG", "").strip()
    if not log_path:
        if os.environ.get("CCB_CODEX_TASK_ID_PROBE_REQUIRED", "0") == "1":
            return CodexTaskIdProbeResult.broken_result(
                binary_realpath=os.environ.get("CCB_CODEX_TASK_ID_PROBE_BINARY", "codex"),
                version=os.environ.get("CCB_CODEX_TASK_ID_PROBE_VERSION", "unknown"),
                binary_mtime_ns=int(os.environ.get("CCB_CODEX_TASK_ID_PROBE_MTIME_NS", "0") or 0),
                completion_log_path="",
                error="startup task_id probe required but CCB_CODEX_TASK_ID_PROBE_LOG is unset",
            )
        return None
    return probe_from_completion_log(
        log_path,
        binary_realpath=os.environ.get("CCB_CODEX_TASK_ID_PROBE_BINARY", "codex"),
        version=os.environ.get("CCB_CODEX_TASK_ID_PROBE_VERSION", "unknown"),
        binary_mtime_ns=int(os.environ.get("CCB_CODEX_TASK_ID_PROBE_MTIME_NS", "0") or 0),
    )


def _first_completion_task_id(path: Path) -> str | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for raw_line in lines:
        if not raw_line.strip():
            continue
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        payload_type = str(record.get("payload_type") or record.get("type") or "").strip()
        if payload_type not in {"task_complete", "turn_complete", "completed"}:
            continue
        task_id = str(record.get("task_id") or "").strip()
        if task_id:
            return task_id
    return None


__all__ = [
    "BROKEN_STATE",
    "CODEX_TASK_ID_PROBE_SCHEMA_VERSION",
    "PROBE_TIMEOUT_SECONDS",
    "CodexTaskIdProbeResult",
    "apply_configured_startup_task_id_probe",
    "build_probe_cache_key",
    "configured_startup_task_id_probe",
    "probe_from_completion_log",
]
