import json

from storage.jsonl_store import JsonlStore


def test_cached_results_are_identity_returned_and_callers_must_not_mutate(monkeypatch, tmp_path):
    monkeypatch.setenv('CCB_CCBD_READAMP_CACHE', '1')
    path = tmp_path / 'events.jsonl'
    path.write_text(json.dumps({'id': 'a'}) + '\n', encoding='utf-8')

    store = JsonlStore()
    key = ('events', str(path))
    first = store.read_all_cached(path, cache_key=key)
    second = store.read_all_cached(path, cache_key=key)

    assert second is first

    # Negative parity check: the cache deliberately returns its internal list.
    # A future caller that needs mutation must make an explicit copy first.
    second.append({'id': 'mutated-by-caller'})
    third = store.read_all_cached(path, cache_key=key)
    assert third is first
    assert third[-1] == {'id': 'mutated-by-caller'}
