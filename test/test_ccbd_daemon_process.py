from __future__ import annotations

import json
from pathlib import Path

import pytest

from ccbd.daemon_process import CcbdProcessError, _wait_for_ccbd_ready
from ccbd.models import CcbdLease, MountState


class _FakeProcess:
    def __init__(self, *, pid: int, polls: list[int | None], returncode: int | None = None) -> None:
        self.pid = pid
        self._polls = list(polls)
        self.returncode = returncode

    def poll(self) -> int | None:
        if self._polls:
            result = self._polls.pop(0)
            self.returncode = result
            return result
        return self.returncode


def _write_lease(path: Path, *, pid: int, socket_path: Path) -> None:
    lease = CcbdLease(
        project_id='project-1',
        ccbd_pid=pid,
        socket_path=str(socket_path),
        owner_uid=1000,
        boot_id='boot-id',
        started_at='2026-05-06T00:00:00Z',
        last_heartbeat_at='2026-05-06T00:00:00Z',
        mount_state=MountState.MOUNTED,
        generation=1,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(lease.to_record()), encoding='utf-8')


def test_wait_for_ccbd_ready_rejects_old_socket_when_spawned_process_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    socket_path = tmp_path / 'ccbd.sock'
    socket_path.write_text('', encoding='utf-8')
    lease_path = tmp_path / 'lease.json'
    _write_lease(lease_path, pid=111, socket_path=socket_path)
    process = _FakeProcess(pid=222, polls=[1], returncode=1)
    pings: list[str] = []

    class FakeClient:
        def __init__(self, path: Path, *, timeout_s: float) -> None:
            assert path == socket_path
            assert timeout_s == 0.2

        def ping(self, target: str) -> None:
            pings.append(target)

    monkeypatch.setattr('ccbd.daemon_process.CcbdClient', FakeClient)

    with pytest.raises(CcbdProcessError, match='ccbd exited before ready'):
        _wait_for_ccbd_ready(
            process=process,  # type: ignore[arg-type]
            socket_path=socket_path,
            lease_path=lease_path,
            timeout_s=0.01,
        )

    assert pings == ['ccbd']


def test_wait_for_ccbd_ready_accepts_socket_owned_by_spawned_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    socket_path = tmp_path / 'ccbd.sock'
    socket_path.write_text('', encoding='utf-8')
    lease_path = tmp_path / 'lease.json'
    _write_lease(lease_path, pid=222, socket_path=socket_path)
    process = _FakeProcess(pid=222, polls=[None])

    class FakeClient:
        def __init__(self, path: Path, *, timeout_s: float) -> None:
            assert path == socket_path
            assert timeout_s == 0.2

        def ping(self, target: str) -> None:
            assert target == 'ccbd'

    monkeypatch.setattr('ccbd.daemon_process.CcbdClient', FakeClient)

    _wait_for_ccbd_ready(
        process=process,  # type: ignore[arg-type]
        socket_path=socket_path,
        lease_path=lease_path,
        timeout_s=0.01,
    )
