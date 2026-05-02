from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Iterator, Literal


CCBD_STATE_MUTATION_GUARD_SCHEMA_VERSION = 1
MutationClass = Literal["mount_lease", "registry", "jsonl_global_state"]
GUARDED_MUTATION_CLASSES: tuple[MutationClass, ...] = ("mount_lease", "registry", "jsonl_global_state")
ALLOWED_PROVIDER_LOCK_PATTERNS = (
    "lib/provider_backends/codex/launcher_runtime/bridge.py:bridge.lock",
)


class StateMutationGuardError(RuntimeError):
    pass


@dataclass(frozen=True)
class StateMutationGuard:
    schema_version: int
    lock_path: Path
    mutation_class: MutationClass
    owner: str


@contextmanager
def guarded_state_mutation(
    lock_path: Path,
    mutation_class: MutationClass,
    *,
    owner: str,
    fail_close: bool = True,
) -> Iterator[StateMutationGuard]:
    if mutation_class not in GUARDED_MUTATION_CLASSES:
        raise StateMutationGuardError(f"unsupported state mutation class: {mutation_class}")
    with _exclusive_file_lock(lock_path, fail_close=fail_close):
        yield StateMutationGuard(
            schema_version=CCBD_STATE_MUTATION_GUARD_SCHEMA_VERSION,
            lock_path=lock_path,
            mutation_class=mutation_class,
            owner=owner,
        )


@contextmanager
def _exclusive_file_lock(lock_path: Path, *, fail_close: bool) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    try:
        try:
            _flock(fd, exclusive=True, nonblocking=True)
            acquired = True
        except OSError as exc:
            if fail_close:
                raise StateMutationGuardError(f"state mutation lock unavailable: {lock_path}") from exc
        yield
    finally:
        if acquired:
            try:
                _flock(fd, exclusive=False, nonblocking=False)
            except OSError:
                pass
        os.close(fd)


def _flock(fd: int, *, exclusive: bool, nonblocking: bool) -> None:
    if os.name == "nt":
        import msvcrt

        mode = msvcrt.LK_NBLCK if exclusive and nonblocking else msvcrt.LK_LOCK if exclusive else msvcrt.LK_UNLCK
        msvcrt.locking(fd, mode, 1)
        return

    import fcntl

    if exclusive:
        operation = fcntl.LOCK_EX
        if nonblocking:
            operation |= fcntl.LOCK_NB
    else:
        operation = fcntl.LOCK_UN
    fcntl.flock(fd, operation)


__all__ = [
    "ALLOWED_PROVIDER_LOCK_PATTERNS",
    "CCBD_STATE_MUTATION_GUARD_SCHEMA_VERSION",
    "GUARDED_MUTATION_CLASSES",
    "MutationClass",
    "StateMutationGuard",
    "StateMutationGuardError",
    "guarded_state_mutation",
]
