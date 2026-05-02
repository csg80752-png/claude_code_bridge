from __future__ import annotations

import time
from pathlib import Path

from ..session_selection import scan_latest


def ensure_log(reader, current_path: Path | None) -> Path:
    candidates = [
        _existing_isolated_candidate(reader, reader._preferred_log, preferred=True),
        _existing_isolated_candidate(reader, current_path),
    ]
    for candidate in candidates:
        if candidate:
            return candidate
    latest = scan_latest(reader)
    if latest:
        reader._preferred_log = latest
        return latest
    raise FileNotFoundError("Codex session log not found")


def _existing_isolated_candidate(reader, path: Path | None, *, preferred: bool = False) -> Path | None:
    if not path or not path.exists():
        return None
    if not _isolated_to_root(reader):
        return path
    if _path_is_under(path, reader.root):
        return path
    if preferred:
        reader._preferred_log = None
    return None


def _isolated_to_root(reader) -> bool:
    return bool(getattr(reader, "_isolated_to_root", False))


def _path_is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def maybe_switch_logs(
    reader,
    *,
    log_path: Path,
    current_path: Path | None,
    offset: int,
    last_rescan: float,
    rescan_interval: float,
) -> tuple[bool, Path | None, int, float]:
    if time.time() - last_rescan < rescan_interval:
        return False, current_path, offset, last_rescan

    latest = scan_latest(reader)
    if latest and latest != log_path:
        current_path = latest
        reader._preferred_log = latest
        return True, current_path, 0, time.time()
    return False, current_path, offset, time.time()


__all__ = ["ensure_log", "maybe_switch_logs"]
