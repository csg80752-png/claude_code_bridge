from __future__ import annotations

import time
from pathlib import Path

from ccbd.models import CcbdLease, MountState, SCHEMA_VERSION
from ccbd.system import current_uid, read_boot_id, utc_now
from runtime_env import env_default_on
from storage.json_store import JsonStore
from storage.paths import PathLayout

_DIRTY_CHECK_ENV = 'CCB_CCBD_DIRTY_CHECK'
# Must stay strictly less than OwnershipGuard's 15s heartbeat_grace_seconds; the
# 10s margin gives a stalled writer one full flush cycle before grace expires.
_LEASE_HEARTBEAT_FLUSH_INTERVAL_S = 5.0


class MountManager:
    def __init__(
        self,
        layout: PathLayout,
        store: JsonStore | None = None,
        *,
        clock=utc_now,
        uid_getter=current_uid,
        boot_id_getter=read_boot_id,
    ) -> None:
        self._layout = layout
        self._store = store or JsonStore()
        self._clock = clock
        self._uid_getter = uid_getter
        self._boot_id_getter = boot_id_getter
        # Lock-free; assumes the mount lifecycle is driven from a single loop.
        self._last_heartbeat_flush_at: float | None = None

    def _save_lease(self, lease: CcbdLease) -> CcbdLease:
        self._store.save(self._layout.ccbd_lease_path, lease, serializer=lambda value: value.to_record())
        self._last_heartbeat_flush_at = time.monotonic()
        return lease

    def load_state(self) -> CcbdLease | None:
        path = self._layout.ccbd_lease_path
        if not path.exists():
            return None
        return self._store.load(path, loader=_lease_from_record)

    def mark_mounted(
        self,
        *,
        project_id: str,
        pid: int,
        socket_path: str | Path,
        generation: int,
        started_at: str | None = None,
        config_signature: str | None = None,
        keeper_pid: int | None = None,
        daemon_instance_id: str | None = None,
    ) -> CcbdLease:
        timestamp = started_at or self._clock()
        lease = CcbdLease(
            project_id=project_id,
            ccbd_pid=pid,
            socket_path=str(socket_path),
            owner_uid=self._uid_getter(),
            boot_id=self._boot_id_getter(),
            started_at=timestamp,
            last_heartbeat_at=timestamp,
            mount_state=MountState.MOUNTED,
            generation=generation,
            config_signature=(str(config_signature).strip() or None) if config_signature is not None else None,
            keeper_pid=int(keeper_pid) if keeper_pid else None,
            daemon_instance_id=(str(daemon_instance_id).strip() or None) if daemon_instance_id is not None else None,
        )
        return self._save_lease(lease)

    def refresh_heartbeat(self) -> CcbdLease:
        lease = self.load_state()
        if lease is None:
            raise RuntimeError('ccbd lease does not exist')
        if lease.mount_state is not MountState.MOUNTED:
            return lease
        updated = lease.with_heartbeat(self._clock())
        if env_default_on(_DIRTY_CHECK_ENV):
            last = self._last_heartbeat_flush_at
            if last is not None and (time.monotonic() - last) < _LEASE_HEARTBEAT_FLUSH_INTERVAL_S:
                return updated
        return self._save_lease(updated)

    def mark_unmounted(self) -> CcbdLease | None:
        lease = self.load_state()
        if lease is None:
            return None
        updated = lease.with_mount_state(MountState.UNMOUNTED, heartbeat_at=self._clock())
        return self._save_lease(updated)


def _lease_from_record(record: dict) -> CcbdLease:
    if record.get('schema_version') != SCHEMA_VERSION:
        raise ValueError(f'schema_version must be {SCHEMA_VERSION}')
    if record.get('record_type') != 'ccbd_lease':
        raise ValueError("record_type must be 'ccbd_lease'")
    return CcbdLease(
        project_id=record['project_id'],
        ccbd_pid=int(record['ccbd_pid']),
        socket_path=record['socket_path'],
        owner_uid=int(record['owner_uid']),
        boot_id=record['boot_id'],
        started_at=record['started_at'],
        last_heartbeat_at=record['last_heartbeat_at'],
        mount_state=MountState(record['mount_state']),
        generation=int(record.get('generation', 1)),
        config_signature=str(record.get('config_signature') or '').strip() or None,
        keeper_pid=int(record['keeper_pid']) if record.get('keeper_pid') else None,
        daemon_instance_id=str(record.get('daemon_instance_id') or '').strip() or None,
        api_version=int(record.get('api_version', 2)),
    )


__all__ = ['MountManager']
