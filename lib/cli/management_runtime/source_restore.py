from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
import hashlib
import os
from pathlib import Path
import shutil
from typing import Literal


DEFAULT_INSTALL_TREE_EXCLUDES = (
    ".git/",
    "__pycache__/",
    "*.pyc",
    "*.pyo",
    ".pytest_cache/",
    ".ruff_cache/",
    ".mypy_cache/",
    ".cache/",
    ".venv/",
    "node_modules/",
    "lib/web/",
    "bin/ccb-web",
    ".ccb/agents/",
    ".ccb/ccbd/",
    ".ccb/history/",
    ".ccb/metrics/",
    ".ccb/replies/",
    ".ccb/*-session",
    ".codex",
    ".codex/",
    ".gemini/",
    ".claude/",
    ".loop/",
    ".context/",
    ".commit-checklist.md",
    "*.bak-*",
)
INSTALL_MANIFEST_NAME = ".ccb-install-manifest.sha256"


@dataclass(frozen=True)
class SourceRestoreDiff:
    missing_from_source: tuple[str, ...]
    modified: tuple[str, ...]
    source_only: tuple[str, ...]


SourceOnlyAction = Literal["delete", "quarantine", "allowlist"]


@dataclass(frozen=True)
class SourceOnlyDisposition:
    action: SourceOnlyAction
    reason: str
    quarantine_root: Path | None = None


@dataclass(frozen=True)
class SourceOnlyResolution:
    action: SourceOnlyAction
    reason: str
    path: Path


@dataclass(frozen=True)
class SourceRestoreResult:
    diff: SourceRestoreDiff
    copied: tuple[str, ...]
    source_only_resolutions: dict[str, SourceOnlyResolution]


@dataclass(frozen=True)
class ManifestSeedResult:
    path: Path
    file_count: int


def classify_source_restore(
    live_root: Path,
    source_root: Path,
    *,
    exclude_patterns: tuple[str, ...] = DEFAULT_INSTALL_TREE_EXCLUDES,
) -> SourceRestoreDiff:
    live_hashes = tree_hashes(live_root, exclude_patterns=exclude_patterns)
    source_hashes = tree_hashes(source_root, exclude_patterns=exclude_patterns)
    missing_from_source = tuple(sorted(rel for rel in live_hashes if rel not in source_hashes))
    modified = tuple(sorted(rel for rel in live_hashes if rel in source_hashes and live_hashes[rel] != source_hashes[rel]))
    source_only = tuple(sorted(rel for rel in source_hashes if rel not in live_hashes))
    return SourceRestoreDiff(
        missing_from_source=missing_from_source,
        modified=modified,
        source_only=source_only,
    )


def restore_source_tree(
    live_root: Path,
    source_root: Path,
    *,
    source_only_dispositions: dict[str, SourceOnlyDisposition] | None = None,
    exclude_patterns: tuple[str, ...] = DEFAULT_INSTALL_TREE_EXCLUDES,
) -> SourceRestoreResult:
    diff = classify_source_restore(live_root, source_root, exclude_patterns=exclude_patterns)
    dispositions = source_only_dispositions or {}
    unresolved = [rel for rel in diff.source_only if rel not in dispositions]
    if unresolved:
        joined = ", ".join(unresolved)
        raise RuntimeError(f"unresolved source-only paths before manifest seed: {joined}")

    copied: list[str] = []
    for rel in (*diff.missing_from_source, *diff.modified):
        _copy_entry(live_root / rel, source_root / rel)
        copied.append(rel)

    resolutions: dict[str, SourceOnlyResolution] = {}
    for rel in diff.source_only:
        disposition = dispositions[rel]
        resolutions[rel] = _resolve_source_only(source_root, rel, disposition)

    return SourceRestoreResult(
        diff=diff,
        copied=tuple(sorted(copied)),
        source_only_resolutions=resolutions,
    )


def seed_install_manifest(
    root: Path,
    *,
    manifest_path: Path | None = None,
    exclude_patterns: tuple[str, ...] = DEFAULT_INSTALL_TREE_EXCLUDES,
) -> ManifestSeedResult:
    manifest = manifest_path or root / INSTALL_MANIFEST_NAME
    hashes = tree_hashes(root, exclude_patterns=exclude_patterns)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = manifest.with_name(f"{manifest.name}.tmp.{os.getpid()}")
    with tmp_path.open("w", encoding="utf-8") as handle:
        for rel in sorted(hashes):
            handle.write(f"{hashes[rel]}  {rel}\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, manifest)
    _fsync_dir(manifest.parent)
    return ManifestSeedResult(path=manifest, file_count=len(hashes))


def tree_hashes(
    root: Path,
    *,
    exclude_patterns: tuple[str, ...] = DEFAULT_INSTALL_TREE_EXCLUDES,
) -> dict[str, str]:
    return {rel: _entry_hash(root / rel) for rel in _scoped_entries(root, exclude_patterns=exclude_patterns)}


def _scoped_entries(root: Path, *, exclude_patterns: tuple[str, ...]) -> tuple[str, ...]:
    if not root.exists():
        return ()
    entries: list[str] = []
    for path in root.rglob("*"):
        rel = path.relative_to(root).as_posix()
        if _is_excluded(rel, exclude_patterns=exclude_patterns):
            continue
        if path.is_symlink() or path.is_file():
            entries.append(rel)
    return tuple(sorted(set(entries)))


def _is_excluded(rel: str, *, exclude_patterns: tuple[str, ...]) -> bool:
    path = Path(rel)
    if path.name == INSTALL_MANIFEST_NAME or path.name.startswith(f"{INSTALL_MANIFEST_NAME}.tmp."):
        return True
    return any(_matches_exclude_pattern(rel, path, pattern) for pattern in exclude_patterns)


def _matches_exclude_pattern(rel: str, path: Path, pattern: str) -> bool:
    normalized = pattern.rstrip("/")
    if not normalized:
        return False
    if pattern.endswith("/"):
        if "/" not in normalized:
            return normalized in path.parts
        return rel == normalized or rel.startswith(f"{normalized}/")
    return fnmatch(path.name, pattern) or fnmatch(rel, pattern)


def _entry_hash(path: Path) -> str:
    if path.is_symlink():
        return _hash_bytes(f"symlink\0{os.readlink(path)}".encode("utf-8"))
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_bytes(payload: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(payload)
    return digest.hexdigest()


def _copy_entry(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    if src.is_symlink():
        os.symlink(os.readlink(src), dst)
        _fsync_dir(dst.parent)
        return
    shutil.copy2(src, dst)
    _fsync_file(dst)
    _fsync_dir(dst.parent)


def _resolve_source_only(source_root: Path, rel: str, disposition: SourceOnlyDisposition) -> SourceOnlyResolution:
    source_path = source_root / rel
    if disposition.action == "allowlist":
        return SourceOnlyResolution(action="allowlist", reason=disposition.reason, path=source_path)
    if disposition.action == "delete":
        source_path.unlink(missing_ok=True)
        _fsync_dir(source_path.parent)
        return SourceOnlyResolution(action="delete", reason=disposition.reason, path=source_path)
    if disposition.action == "quarantine":
        if disposition.quarantine_root is None:
            raise RuntimeError(f"quarantine_root required for source-only path: {rel}")
        quarantine_path = disposition.quarantine_root / rel
        quarantine_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source_path, quarantine_path)
        _fsync_dir(source_path.parent)
        _fsync_dir(quarantine_path.parent)
        return SourceOnlyResolution(action="quarantine", reason=disposition.reason, path=quarantine_path)
    raise RuntimeError(f"unknown source-only disposition: {disposition.action}")


def _fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


__all__ = [
    "DEFAULT_INSTALL_TREE_EXCLUDES",
    "INSTALL_MANIFEST_NAME",
    "ManifestSeedResult",
    "SourceOnlyDisposition",
    "SourceOnlyResolution",
    "SourceRestoreDiff",
    "SourceRestoreResult",
    "classify_source_restore",
    "restore_source_tree",
    "seed_install_manifest",
    "tree_hashes",
]
