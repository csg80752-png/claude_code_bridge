import json
import os

from storage.jsonl_store import JsonlStore


def _write_one(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(row, sort_keys=True) + '\n', encoding='utf-8')


def test_same_size_rewrite_invalidates_on_mtime_change(monkeypatch, tmp_path):
    monkeypatch.setenv('CCB_CCBD_READAMP_CACHE', '1')
    path = tmp_path / 'events.jsonl'
    _write_one(path, {'id': 'a', 'value': 'xx'})

    store = JsonlStore()
    first = store.read_all_cached(path, cache_key=('events', str(path)))
    assert first == [{'id': 'a', 'value': 'xx'}]
    before = path.stat()

    _write_one(path, {'id': 'b', 'value': 'xx'})
    after = path.stat()
    if after.st_mtime_ns == before.st_mtime_ns:
        os.utime(path, ns=(before.st_atime_ns + 1_000_000, before.st_mtime_ns + 1_000_000))

    assert path.stat().st_size == before.st_size
    second = store.read_all_cached(path, cache_key=('events', str(path)))
    assert second is not first
    assert second == [{'id': 'b', 'value': 'xx'}]
