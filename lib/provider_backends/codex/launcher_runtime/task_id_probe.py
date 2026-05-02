from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
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
        runtime_state["requires_task_id"] = True
        runtime_state["task_id_probe_cache_key"] = build_probe_cache_key(
            result.binary_realpath,
            result.version,
            result.binary_mtime_ns,
        )
        runtime_state["codex_task_id_probe_state"] = BROKEN_STATE
        runtime_state["codex_task_id_probe_artifact"] = result.to_record()
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
    if _env_truthy("CCB_CODEX_TASK_ID_PROBE_DISABLED"):
        return None

    log_path = os.environ.get("CCB_CODEX_TASK_ID_PROBE_LOG", "").strip()
    if not log_path:
        binary = discover_codex_binary()
        if binary is None:
            return CodexTaskIdProbeResult.broken_result(
                binary_realpath="codex",
                version="unknown",
                binary_mtime_ns=0,
                completion_log_path="",
                error="no installed Codex CLI found for startup task_id probe",
            )
        return probe_installed_codex_cli(binary)
    return probe_from_completion_log(
        log_path,
        binary_realpath=os.environ.get("CCB_CODEX_TASK_ID_PROBE_BINARY", "codex"),
        version=os.environ.get("CCB_CODEX_TASK_ID_PROBE_VERSION", "unknown"),
        binary_mtime_ns=int(os.environ.get("CCB_CODEX_TASK_ID_PROBE_MTIME_NS", "0") or 0),
    )


def discover_codex_binary() -> Path | None:
    candidates: list[Path] = []
    env_binary = os.environ.get("CCB_CODEX_TASK_ID_PROBE_BINARY", "").strip()
    if env_binary:
        resolved_env_binary = shutil.which(env_binary) if not Path(env_binary).is_absolute() else env_binary
        if resolved_env_binary:
            candidates.append(Path(resolved_env_binary).expanduser())

    path_binary = shutil.which("codex")
    if path_binary:
        candidates.append(Path(path_binary))

    for prefix in _known_codex_install_prefixes():
        candidates.extend((prefix / "codex", prefix / "bin" / "codex"))

    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        candidate_key = str(resolved)
        if candidate_key in seen:
            continue
        seen.add(candidate_key)
        if _is_executable_file(resolved):
            return resolved
    return None


def probe_installed_codex_cli(
    binary_path: str | Path,
    *,
    probe_timeout_seconds: int = PROBE_TIMEOUT_SECONDS,
) -> CodexTaskIdProbeResult:
    binary = Path(binary_path).expanduser().resolve()
    try:
        stat_result = binary.stat()
    except OSError as exc:
        return CodexTaskIdProbeResult.broken_result(
            binary_realpath=str(binary),
            version="unknown",
            binary_mtime_ns=0,
            completion_log_path="",
            error=f"Codex CLI stat failed: {exc}",
            probe_timeout_seconds=probe_timeout_seconds,
        )

    version = _codex_version(binary, probe_timeout_seconds=probe_timeout_seconds)
    probe_log_path = _new_probe_log_path()
    command = [
        str(binary),
        "--ask-for-approval",
        "never",
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--ignore-rules",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "-",
    ]
    try:
        completed = subprocess.run(
            command,
            input="echo probe\n",
            capture_output=True,
            text=True,
            timeout=probe_timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        _write_probe_log(probe_log_path, exc.stdout)
        return CodexTaskIdProbeResult.broken_result(
            binary_realpath=str(binary),
            version=version,
            binary_mtime_ns=stat_result.st_mtime_ns,
            completion_log_path=str(probe_log_path),
            error=f"Codex CLI startup task_id probe timed out after {probe_timeout_seconds}s",
            probe_timeout_seconds=probe_timeout_seconds,
        )
    except OSError as exc:
        return CodexTaskIdProbeResult.broken_result(
            binary_realpath=str(binary),
            version=version,
            binary_mtime_ns=stat_result.st_mtime_ns,
            completion_log_path=str(probe_log_path),
            error=f"Codex CLI startup task_id probe failed to execute: {exc}",
            probe_timeout_seconds=probe_timeout_seconds,
        )

    _write_probe_log(probe_log_path, completed.stdout)
    if completed.returncode != 0:
        return CodexTaskIdProbeResult.broken_result(
            binary_realpath=str(binary),
            version=version,
            binary_mtime_ns=stat_result.st_mtime_ns,
            completion_log_path=str(probe_log_path),
            error=f"Codex CLI startup task_id probe exited {completed.returncode}: {_first_line(completed.stderr)}",
            probe_timeout_seconds=probe_timeout_seconds,
        )

    return probe_from_completion_log(
        probe_log_path,
        binary_realpath=str(binary),
        version=version,
        binary_mtime_ns=stat_result.st_mtime_ns,
        probe_timeout_seconds=probe_timeout_seconds,
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
        if not _is_completion_record(record):
            continue
        task_id = _record_task_id(record)
        if task_id:
            return task_id
    return None


def _is_completion_record(record: dict[str, Any]) -> bool:
    payload = record.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    raw_type = str(record.get("payload_type") or payload.get("type") or record.get("type") or "").strip()
    normalized = raw_type.replace(".", "_").replace("-", "_")
    return normalized in {"task_complete", "turn_complete", "completed"}


def _record_task_id(record: dict[str, Any]) -> str:
    payload = record.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    return str(record.get("task_id") or payload.get("task_id") or "").strip()


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _known_codex_install_prefixes() -> tuple[Path, ...]:
    live_prefix = Path("/home/speed/.local/share/codex-dual")
    prefixes = [live_prefix]
    try:
        prefixes.append(live_prefix.resolve())
    except OSError:
        pass
    return tuple(prefixes)


def _is_executable_file(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _codex_version(binary: Path, *, probe_timeout_seconds: int) -> str:
    try:
        completed = subprocess.run(
            [str(binary), "--version"],
            capture_output=True,
            text=True,
            timeout=probe_timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    version = _first_line(completed.stdout) or _first_line(completed.stderr)
    return version or "unknown"


def _new_probe_log_path() -> Path:
    fd, raw_path = tempfile.mkstemp(prefix="ccb-codex-task-id-probe-", suffix=".jsonl")
    os.close(fd)
    return Path(raw_path)


def _write_probe_log(path: Path, content: object) -> None:
    if isinstance(content, bytes):
        text = content.decode("utf-8", errors="replace")
    else:
        text = str(content or "")
    path.write_text(text, encoding="utf-8")


def _first_line(text: object) -> str:
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    return str(text or "").strip().splitlines()[0].strip() if str(text or "").strip() else ""


__all__ = [
    "BROKEN_STATE",
    "CODEX_TASK_ID_PROBE_SCHEMA_VERSION",
    "PROBE_TIMEOUT_SECONDS",
    "CodexTaskIdProbeResult",
    "apply_configured_startup_task_id_probe",
    "build_probe_cache_key",
    "configured_startup_task_id_probe",
    "discover_codex_binary",
    "probe_installed_codex_cli",
    "probe_from_completion_log",
]
