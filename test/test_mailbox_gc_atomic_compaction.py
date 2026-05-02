from __future__ import annotations

import json
from pathlib import Path

from mailbox_kernel.gc import MAX_MAILBOX_BYTES, compact_jsonl_file_atomic
from storage.jsonl_store import JsonlStore


def _rows(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_compaction_invalidates_jsonl_cache_and_uses_atomic_rewrite(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CCB_CCBD_READAMP_CACHE", "1")
    path = tmp_path / ".ccb" / "ccbd" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"id":"old"}\n{"id":"keep"}\n', encoding="utf-8")
    store = JsonlStore()
    key = ("events", str(path))
    assert [row["id"] for row in store.read_all_cached(path, cache_key=key)] == ["old", "keep"]

    compact_jsonl_file_atomic(path, rows=[{"id": "keep"}], cache_owner=store, cache_keys=(key,))

    assert [row["id"] for row in store.read_all_cached(path, cache_key=key)] == ["keep"]
    assert not list(path.parent.glob("*.tmp.*"))


def test_corrupt_rows_are_quarantined_without_blocking_valid_rows(tmp_path: Path) -> None:
    path = tmp_path / ".ccb" / "ccbd" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"id":"a"}\n{bad json\n{"id":"b"}\n', encoding="utf-8")

    compact_jsonl_file_atomic(path, rows=[{"id": "a"}, {"id": "b"}], corrupt_rows=["{bad json\n"])

    assert [row["id"] for row in _rows(path)] == ["a", "b"]
    quarantines = list(path.parent.glob("events.jsonl.corrupt.*.jsonl"))
    assert len(quarantines) == 1
    assert "{bad json" in quarantines[0].read_text(encoding="utf-8")


def test_mailbox_byte_cap_matches_header_ceiling() -> None:
    assert MAX_MAILBOX_BYTES == 9_999_999
