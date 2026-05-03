from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path

from mailbox_kernel import gc as mailbox_gc
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


def test_compaction_invalidates_cache_inside_guard_and_fsyncs_parent_before_and_after_rename(
    monkeypatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / ".ccb" / "ccbd" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"id":"old"}\n', encoding="utf-8")
    events: list[tuple[str, str] | str] = []
    inside_guard = False
    original_replace = mailbox_gc.os.replace

    @contextmanager
    def _guard(path, **kwargs):
        nonlocal inside_guard
        del path, kwargs
        events.append("guard-enter")
        inside_guard = True
        try:
            yield
        finally:
            inside_guard = False
            events.append("guard-exit")

    def _fsync_parent(path):
        del path
        events.append(("fsync-parent", "inside" if inside_guard else "outside"))

    def _replace(src, dst):
        events.append(("replace", "inside" if inside_guard else "outside"))
        original_replace(src, dst)

    class _CacheOwner:
        def invalidate(self, key):
            del key
            events.append(("invalidate", "inside" if inside_guard else "outside"))

    monkeypatch.setattr(mailbox_gc, "guarded_state_mutation_for_path", _guard)
    monkeypatch.setattr(mailbox_gc, "_fsync_parent", _fsync_parent)
    monkeypatch.setattr(mailbox_gc.os, "replace", _replace)

    compact_jsonl_file_atomic(path, rows=[{"id": "new"}], cache_owner=_CacheOwner(), cache_keys=(("events", str(path)),))

    replace_index = events.index(("replace", "inside"))
    fsync_indexes = [index for index, event in enumerate(events) if event == ("fsync-parent", "inside")]
    assert len(fsync_indexes) >= 2
    assert fsync_indexes[0] < replace_index < fsync_indexes[-1]
    assert events.index(("invalidate", "inside")) < events.index("guard-exit")
