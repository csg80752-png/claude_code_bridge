from __future__ import annotations

import time
from dataclasses import fields

from runtime_env import env_default_on
from storage.json_store import JsonStore
from storage.paths import PathLayout

from .records import KeeperState, ShutdownIntent

_DIRTY_CHECK_ENV = 'CCB_CCBD_DIRTY_CHECK'
# Any future KeeperState field that updates on every probe without semantic
# change must be added here AND covered by a flag-on suppression test.
_KEEPER_HEARTBEAT_ONLY_FIELDS = frozenset({'last_check_at'})
_KEEPER_FORCED_FLUSH_INTERVAL_S = 5.0


class KeeperStateStore:
    def __init__(self, layout: PathLayout, store: JsonStore | None = None) -> None:
        self._layout = layout
        self._store = store or JsonStore()
        # Lock-free; assumes the keeper loop is the sole writer to save().
        self._last_saved_state: KeeperState | None = None
        self._last_flush_at: float | None = None

    def load(self) -> KeeperState | None:
        path = self._layout.ccbd_keeper_path
        if not path.exists():
            return None
        return self._store.load(path, loader=KeeperState.from_record)

    def save(self, state: KeeperState) -> None:
        if not env_default_on(_DIRTY_CHECK_ENV):
            self._store.save(self._layout.ccbd_keeper_path, state, serializer=lambda value: value.to_record())
            self._last_saved_state = state
            self._last_flush_at = time.monotonic()
            return

        previous = self._last_saved_state
        meaningful_change = previous is None or any(
            getattr(previous, f.name) != getattr(state, f.name)
            for f in fields(state)
            if f.name not in _KEEPER_HEARTBEAT_ONLY_FIELDS
        )
        flush_due = self._last_flush_at is None or (time.monotonic() - self._last_flush_at) >= _KEEPER_FORCED_FLUSH_INTERVAL_S

        if meaningful_change or flush_due:
            self._store.save(self._layout.ccbd_keeper_path, state, serializer=lambda value: value.to_record())
            self._last_saved_state = state
            self._last_flush_at = time.monotonic()
            return

        self._last_saved_state = state


class ShutdownIntentStore:
    def __init__(self, layout: PathLayout, store: JsonStore | None = None) -> None:
        self._layout = layout
        self._store = store or JsonStore()

    def load(self) -> ShutdownIntent | None:
        path = self._layout.ccbd_shutdown_intent_path
        if not path.exists():
            return None
        return self._store.load(path, loader=ShutdownIntent.from_record)

    def save(self, intent: ShutdownIntent) -> None:
        self._store.save(self._layout.ccbd_shutdown_intent_path, intent, serializer=lambda value: value.to_record())

    def clear(self) -> None:
        try:
            self._layout.ccbd_shutdown_intent_path.unlink()
        except FileNotFoundError:
            pass


__all__ = ['KeeperStateStore', 'ShutdownIntentStore']
