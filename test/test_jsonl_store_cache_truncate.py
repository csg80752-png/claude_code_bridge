import json

from storage.jsonl_store import JsonlStore


def _write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + '\n')


def test_truncate_invalidates_cache(monkeypatch, tmp_path):
    monkeypatch.setenv('CCB_CCBD_READAMP_CACHE', '1')
    path = tmp_path / 'events.jsonl'
    _write_rows(path, [{'id': 'alpha', 'payload': 'long'}, {'id': 'beta', 'payload': 'long'}])

    store = JsonlStore()
    first = store.read_all_cached(path, cache_key=('events', str(path)))
    assert [row['id'] for row in first] == ['alpha', 'beta']

    _write_rows(path, [{'id': 'z'}])
    second = store.read_all_cached(path, cache_key=('events', str(path)))
    assert second is not first
    assert [row['id'] for row in second] == ['z']
