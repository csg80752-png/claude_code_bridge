import json

from storage import jsonl_store as jsonl_store_module
from storage.jsonl_store import JsonlStore


def test_env_is_read_once_at_store_construction(monkeypatch, tmp_path):
    path = tmp_path / 'events.jsonl'
    path.write_text(json.dumps({'id': 'a'}) + '\n', encoding='utf-8')

    monkeypatch.setenv('CCB_CCBD_READAMP_CACHE', '1')
    caching_store = JsonlStore()
    monkeypatch.delenv('CCB_CCBD_READAMP_CACHE', raising=False)
    default_caching_store = JsonlStore()
    monkeypatch.setenv('CCB_CCBD_READAMP_CACHE', '0')
    disabled_store = JsonlStore()

    calls = []
    real_loads = jsonl_store_module.json.loads

    def counted_loads(text):
        calls.append(text)
        return real_loads(text)

    monkeypatch.setattr(jsonl_store_module.json, 'loads', counted_loads)

    key = ('events', str(path))
    caching_store.read_all_cached(path, cache_key=key)
    caching_store.read_all_cached(path, cache_key=key)
    assert len(calls) == 1
    assert key in caching_store._cache

    default_caching_store.read_all_cached(path, cache_key=key)
    default_caching_store.read_all_cached(path, cache_key=key)
    assert len(calls) == 2
    assert key in default_caching_store._cache

    disabled_store.read_all_cached(path, cache_key=key)
    disabled_store.read_all_cached(path, cache_key=key)
    assert len(calls) == 4
    assert disabled_store._cache == {}
