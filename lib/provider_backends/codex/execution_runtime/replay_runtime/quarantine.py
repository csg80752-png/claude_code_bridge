from __future__ import annotations

import errno
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

# Quarantine spec from docs/v8.4-plan.md §"Quarantine requirement for C":
#   ~/.gstack/quarantine/<agent>-<job_id>-<ts>.jsonl    (raw entries tail)
#   ~/.gstack/quarantine/<agent>-<job_id>-<ts>.manifest.json
# Tunables (env vars, all optional):
#   CCB_QUARANTINE_ROOT             - override base directory (test hook)
#   CCB_QUARANTINE_TAIL_ENTRIES     - default 50, last N raw lines to capture
#   CCB_QUARANTINE_TTL_DAYS         - default 7, evict files older than TTL
#   CCB_QUARANTINE_SIZE_CAP_BYTES   - default 100MB, oldest-first eviction
#
# Permission failure is non-blocking: every public function logs a warning
# and returns gracefully so that the abandon path always proceeds (the queue
# must not get stuck on a quarantine I/O error).

_logger = logging.getLogger(__name__)

DEFAULT_TAIL_ENTRIES = 50
DEFAULT_TTL_DAYS = 7
DEFAULT_SIZE_CAP_BYTES = 100_000_000
QUARANTINE_DIR_NAME = "quarantine"
GSTACK_DIR_NAME = ".gstack"

# Same character set the rest of the daemon uses for filename safety. Codex
# job ids are hex/uuid-like already; this defends against weird agent names
# coming through CCB_AGENT_NAME.
_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]")


@dataclass
class QuarantineRecord:
    jsonl_path: Path
    manifest_path: Path
    entries_written: int
    bytes_written: int


def quarantine_root() -> Path:
    override = os.environ.get("CCB_QUARANTINE_ROOT", "").strip()
    if override:
        return Path(override)
    home = os.environ.get("HOME") or os.path.expanduser("~")
    return Path(home) / GSTACK_DIR_NAME / QUARANTINE_DIR_NAME


def write_quarantine(
    *,
    agent: str,
    job_id: str,
    inbound_event_id: str,
    session_path: str | None,
    offset: int,
    failure_reason: str,
    raw_lines: Iterable[str],
    now: float | None = None,
) -> QuarantineRecord | None:
    """Write the trailing raw codex jsonl entries plus a sidecar manifest.

    Returns ``None`` only when the quarantine directory itself cannot be
    created (the abandon path still proceeds — the caller must not block on
    quarantine I/O failure).
    """

    timestamp = now if now is not None else time.time()
    root = quarantine_root()
    if not _ensure_dir(root):
        return None

    _evict_expired(root, now=timestamp)

    tail_entries = _resolve_tail_entries()
    tail = _take_tail(raw_lines, tail_entries)
    safe_agent = _safe_filename_part(agent or "agent")
    safe_job = _safe_filename_part(job_id or "job_unknown")
    ts_token = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(timestamp))
    base = f"{safe_agent}-{safe_job}-{ts_token}"
    jsonl_path = root / f"{base}.jsonl"
    manifest_path = root / f"{base}.manifest.json"

    bytes_written = 0
    entries_written = 0
    try:
        with jsonl_path.open("w", encoding="utf-8") as handle:
            for line in tail:
                normalized = line.rstrip("\n")
                handle.write(normalized + "\n")
                bytes_written += len(normalized) + 1
                entries_written += 1
    except OSError as exc:
        _log_io_warning("quarantine jsonl write failed", jsonl_path, exc)
        return None

    manifest = {
        "agent": agent,
        "job_id": job_id,
        "inbound_event_id": inbound_event_id,
        "session_path": session_path,
        "offset": int(offset),
        "created_at": _isoformat(timestamp),
        "entry_count": entries_written,
        "failure_reason": failure_reason,
        "tail_entries_limit": tail_entries,
    }
    try:
        with manifest_path.open("w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except OSError as exc:
        _log_io_warning("quarantine manifest write failed", manifest_path, exc)
        # The jsonl is on disk but its sidecar is missing. Caller can still
        # see the raw tail; surface a warning and return None so the caller
        # treats this as a quarantine failure for telemetry purposes.
        return None

    _evict_size_cap(root, now=timestamp)

    return QuarantineRecord(
        jsonl_path=jsonl_path,
        manifest_path=manifest_path,
        entries_written=entries_written,
        bytes_written=bytes_written,
    )


def read_tail_lines(jsonl_path: Path | None, max_lines: int) -> list[str]:
    """Return the last ``max_lines`` non-empty lines of ``jsonl_path``.

    Reads from EOF backwards using small chunks so a multi-MB session jsonl
    does not pull the entire file into memory.
    """

    if jsonl_path is None or max_lines <= 0:
        return []
    try:
        with jsonl_path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            position = handle.tell()
            buffer = b""
            collected: list[bytes] = []
            chunk_size = 16 * 1024
            while position > 0 and len(collected) < max_lines:
                read_size = min(chunk_size, position)
                position -= read_size
                handle.seek(position, os.SEEK_SET)
                buffer = handle.read(read_size) + buffer
                lines = buffer.split(b"\n")
                buffer = lines[0]  # incomplete head; keep for next round
                tail_lines = [line for line in lines[1:] if line.strip()]
                if tail_lines:
                    collected = tail_lines + collected
                    if len(collected) >= max_lines:
                        break
            if buffer.strip() and len(collected) < max_lines:
                collected.insert(0, buffer)
            tail = collected[-max_lines:]
            return [line.decode("utf-8", errors="replace") for line in tail]
    except OSError as exc:
        _log_io_warning("quarantine tail read failed", jsonl_path, exc)
        return []


def _resolve_tail_entries() -> int:
    raw = os.environ.get("CCB_QUARANTINE_TAIL_ENTRIES", "").strip()
    if not raw:
        return DEFAULT_TAIL_ENTRIES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_TAIL_ENTRIES
    return max(1, value)


def _resolve_ttl_days() -> int:
    raw = os.environ.get("CCB_QUARANTINE_TTL_DAYS", "").strip()
    if not raw:
        return DEFAULT_TTL_DAYS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_TTL_DAYS
    return max(0, value)


def _resolve_size_cap_bytes() -> int:
    raw = os.environ.get("CCB_QUARANTINE_SIZE_CAP_BYTES", "").strip()
    if not raw:
        return DEFAULT_SIZE_CAP_BYTES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_SIZE_CAP_BYTES
    return max(0, value)


def _ensure_dir(root: Path) -> bool:
    try:
        root.mkdir(parents=True, exist_ok=True)
        return True
    except PermissionError as exc:
        _log_io_warning("quarantine mkdir permission denied", root, exc)
        return False
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            return True
        _log_io_warning("quarantine mkdir failed", root, exc)
        return False


def _evict_expired(root: Path, *, now: float) -> None:
    ttl_days = _resolve_ttl_days()
    if ttl_days <= 0:
        return
    horizon = now - ttl_days * 86400
    for entry in _safe_iter_entries(root):
        try:
            stat = entry.stat()
        except OSError:
            continue
        if stat.st_mtime < horizon:
            _safe_unlink(entry)


def _evict_size_cap(root: Path, *, now: float) -> None:
    cap = _resolve_size_cap_bytes()
    if cap <= 0:
        return
    candidates: list[tuple[float, int, Path]] = []
    total = 0
    for entry in _safe_iter_entries(root):
        try:
            stat = entry.stat()
        except OSError:
            continue
        candidates.append((stat.st_mtime, stat.st_size, entry))
        total += stat.st_size
    if total <= cap:
        return
    candidates.sort(key=lambda item: item[0])
    for _mtime, size, path in candidates:
        if total <= cap:
            return
        if path.name.endswith(".jsonl") or path.name.endswith(".manifest.json"):
            _safe_unlink(path)
            total -= size
            sibling = _sibling_for(path)
            if sibling is not None and sibling.exists():
                try:
                    total -= sibling.stat().st_size
                except OSError:
                    pass
                _safe_unlink(sibling)


def _sibling_for(path: Path) -> Path | None:
    if path.name.endswith(".jsonl"):
        return path.with_suffix(".manifest.json")
    if path.name.endswith(".manifest.json"):
        # ".manifest.json" is two suffixes; .with_suffix only swaps the last
        return path.with_name(path.name[: -len(".manifest.json")] + ".jsonl")
    return None


def _safe_iter_entries(root: Path) -> Iterator[Path]:
    try:
        for entry in root.iterdir():
            if entry.is_file():
                yield entry
    except OSError:
        return


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _take_tail(raw_lines: Iterable[str], limit: int) -> list[str]:
    if limit <= 0:
        return []
    bucket: list[str] = []
    for line in raw_lines:
        if line is None:
            continue
        text = str(line)
        bucket.append(text)
        if len(bucket) > limit:
            bucket.pop(0)
    return bucket


def _safe_filename_part(value: str) -> str:
    sanitized = _FILENAME_SAFE.sub("_", value)
    return sanitized or "_"


def _isoformat(timestamp: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(timestamp))


def _log_io_warning(prefix: str, path: Path, exc: OSError) -> None:
    _logger.warning("%s path=%s err=%s", prefix, path, exc)


__all__ = [
    "DEFAULT_SIZE_CAP_BYTES",
    "DEFAULT_TAIL_ENTRIES",
    "DEFAULT_TTL_DAYS",
    "QuarantineRecord",
    "quarantine_root",
    "read_tail_lines",
    "write_quarantine",
]
