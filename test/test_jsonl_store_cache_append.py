import json

from storage import jsonl_store as jsonl_store_module
from storage.jsonl_store import JsonlStore


def _append_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + '\n')


def test_append_reads_only_new_rows(monkeypatch, tmp_path):
    monkeypatch.setenv('CCB_CCBD_READAMP_CACHE', '1')
    path = tmp_path / 'events.jsonl'
    _append_rows(path, [{'id': 'a'}, {'id': 'b'}])

    store = JsonlStore()
    calls = []
    real_loads = jsonl_store_module.json.loads

    def counted_loads(text):
        calls.append(text)
        return real_loads(text)

    monkeypatch.setattr(jsonl_store_module.json, 'loads', counted_loads)

    first = store.read_all_cached(path, cache_key=('events', str(path)))
    assert [row['id'] for row in first] == ['a', 'b']
    assert len(calls) == 2

    _append_rows(path, [{'id': 'c'}])
    second = store.read_all_cached(path, cache_key=('events', str(path)))
    assert second is first
    assert [row['id'] for row in second] == ['a', 'b', 'c']
    assert len(calls) == 3
