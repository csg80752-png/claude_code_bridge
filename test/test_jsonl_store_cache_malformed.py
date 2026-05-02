import json

import pytest

from storage.jsonl_store import JsonlStore


def test_malformed_delta_raises_and_preserves_prior_cache(monkeypatch, tmp_path):
    monkeypatch.setenv('CCB_CCBD_READAMP_CACHE', '1')
    path = tmp_path / 'events.jsonl'
    path.write_text(json.dumps({'id': 'a'}) + '\n', encoding='utf-8')
    key = ('events', str(path))

    store = JsonlStore()
    first = store.read_all_cached(path, cache_key=key)
    assert first == [{'id': 'a'}]

    with path.open('a', encoding='utf-8') as handle:
        handle.write('{not-json\n')

    with pytest.raises(json.JSONDecodeError):
        store.read_all_cached(path, cache_key=key)

    assert store._cache[key].rows is first
    assert store._cache[key].rows == [{'id': 'a'}]
