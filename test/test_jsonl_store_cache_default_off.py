import json

from storage import jsonl_store as jsonl_store_module
from storage.jsonl_store import JsonlStore


def test_explicit_zero_falls_through_to_read_all_and_keeps_cache_empty(monkeypatch, tmp_path):
    monkeypatch.setenv('CCB_CCBD_READAMP_CACHE', '0')
    path = tmp_path / 'events.jsonl'
    path.write_text(json.dumps({'id': 'a'}) + '\n', encoding='utf-8')

    store = JsonlStore()
    calls = []
    real_loads = jsonl_store_module.json.loads

    def counted_loads(text):
        calls.append(text)
        return real_loads(text)

    monkeypatch.setattr(jsonl_store_module.json, 'loads', counted_loads)

    first = store.read_all_cached(path, cache_key=('events', str(path)))
    second = store.read_all_cached(path, cache_key=('events', str(path)))

    assert first == second == [{'id': 'a'}]
    assert first is not second
    assert len(calls) == 2
    assert store._cache == {}


def test_unset_env_defaults_to_cache_enabled(monkeypatch, tmp_path):
    monkeypatch.delenv('CCB_CCBD_READAMP_CACHE', raising=False)
    path = tmp_path / 'events.jsonl'
    path.write_text(json.dumps({'id': 'a'}) + '\n', encoding='utf-8')

    store = JsonlStore()
    calls = []
    real_loads = jsonl_store_module.json.loads

    def counted_loads(text):
        calls.append(text)
        return real_loads(text)

    monkeypatch.setattr(jsonl_store_module.json, 'loads', counted_loads)

    key = ('events', str(path))
    first = store.read_all_cached(path, cache_key=key)
    second = store.read_all_cached(path, cache_key=key)

    assert first is second
    assert len(calls) == 1
    assert key in store._cache
