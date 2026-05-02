import json

from storage.jsonl_store import JsonlStore


def test_missing_file_returns_empty_and_clears_cache(monkeypatch, tmp_path):
    monkeypatch.setenv('CCB_CCBD_READAMP_CACHE', '1')
    path = tmp_path / 'events.jsonl'
    path.write_text(json.dumps({'id': 'a'}) + '\n', encoding='utf-8')
    key = ('events', str(path))

    store = JsonlStore()
    assert store.read_all_cached(path, cache_key=key) == [{'id': 'a'}]
    assert key in store._cache

    path.unlink()
    assert store.read_all_cached(path, cache_key=key) == []
    assert key not in store._cache
