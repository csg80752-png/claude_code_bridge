from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TypeVar

from runtime_env import env_default_on

T = TypeVar('T')

_CACHE_ENV = 'CCB_CCBD_READAMP_CACHE'


@dataclass
class _JsonlCacheEntry:
    size: int
    mtime_ns: int
    line_count: int
    rows: list[Any] = field(default_factory=list)


class JsonlStore:
    def __init__(self) -> None:
        self._cache: dict[Any, _JsonlCacheEntry] = {}
        self._cache_enabled = env_default_on(_CACHE_ENV)

    def append(
        self,
        path: Path,
        row: T | dict[str, Any],
        serializer: Callable[[T], dict[str, Any]] | None = None,
    ) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if serializer is None:
            if not isinstance(row, dict):
                raise ValueError('serializer is required for non-dict rows')
            payload = row
        else:
            payload = serializer(row)
        from ccbd.state_mutation_guard import guarded_state_mutation_for_path

        with guarded_state_mutation_for_path(target, owner='storage.jsonl_store', blocking=True):
            with target.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + '\n')

    def read_all(self, path: Path, loader: Callable[[dict[str, Any]], T] | None = None) -> list[T] | list[dict[str, Any]]:
        target = Path(path)
        if not target.exists():
            return []
        rows: list[T] | list[dict[str, Any]] = []
        with target.open('r', encoding='utf-8') as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                payload = json.loads(text)
                if not isinstance(payload, dict):
                    raise ValueError(f'{path}: expected JSON object rows')
                rows.append(loader(payload) if loader else payload)
        return rows

    def read_latest_valid(
        self,
        path: Path,
        loader: Callable[[dict[str, Any]], T] | None = None,
    ) -> T | dict[str, Any] | None:
        target = Path(path)
        if not target.exists():
            return None
        for line in reversed(target.read_text(encoding='utf-8', errors='replace').splitlines()):
            text = line.strip().strip('\x00')
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            try:
                return loader(payload) if loader else payload
            except (KeyError, TypeError, ValueError):
                continue
        return None

    def read_since(
        self,
        path: Path,
        start_line: int = 0,
        loader: Callable[[dict[str, Any]], T] | None = None,
    ) -> tuple[int, list[T] | list[dict[str, Any]]]:
        if start_line < 0:
            raise ValueError('start_line cannot be negative')
        target = Path(path)
        if not target.exists():
            return start_line, []
        rows: list[T] | list[dict[str, Any]] = []
        current = 0
        with target.open('r', encoding='utf-8') as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                current += 1
                if current <= start_line:
                    continue
                payload = json.loads(text)
                if not isinstance(payload, dict):
                    raise ValueError(f'{path}: expected JSON object rows')
                rows.append(loader(payload) if loader else payload)
        return current, rows

    def read_all_cached(
        self,
        path: Path,
        *,
        loader: Callable[[dict[str, Any]], T] | None = None,
        cache_key: Any,
    ) -> list[T] | list[dict[str, Any]]:
        """Read a JSONL file with an append-aware stat cache.

        When caching is enabled, cache hits return the internal cached list. Callers
        must treat the returned list as read-only or explicitly copy it before
        mutation.
        """
        if not self._cache_enabled:
            return self.read_all(path, loader=loader)

        target = Path(path)
        try:
            stat_result = target.stat()
        except FileNotFoundError:
            self._cache.pop(cache_key, None)
            return []

        size = stat_result.st_size
        mtime_ns = stat_result.st_mtime_ns
        entry = self._cache.get(cache_key)

        if entry is not None and size < entry.size:
            self._cache.pop(cache_key, None)
            entry = None

        if entry is not None and size == entry.size and mtime_ns == entry.mtime_ns:
            return entry.rows

        if entry is not None and size == entry.size and mtime_ns != entry.mtime_ns:
            self._cache.pop(cache_key, None)
            entry = None

        if entry is None:
            line_count, rows = self.read_since(target, start_line=0, loader=loader)
            cached = _JsonlCacheEntry(
                size=size,
                mtime_ns=mtime_ns,
                line_count=line_count,
                rows=list(rows),
            )
            self._cache[cache_key] = cached
            return cached.rows

        line_count, new_rows = self.read_since(target, start_line=entry.line_count, loader=loader)
        entry.rows.extend(new_rows)
        entry.line_count = line_count
        entry.size = size
        entry.mtime_ns = mtime_ns
        return entry.rows

    def invalidate(self, cache_key: Any) -> None:
        self._cache.pop(cache_key, None)
