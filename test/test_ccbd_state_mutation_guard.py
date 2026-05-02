from __future__ import annotations

import fcntl
from pathlib import Path

import pytest

from ccbd.state_mutation_guard import (
    ALLOWED_PROVIDER_LOCK_PATTERNS,
    CCBD_STATE_MUTATION_GUARD_SCHEMA_VERSION,
    GUARDED_MUTATION_CLASSES,
    StateMutationGuard,
    StateMutationGuardError,
    guarded_state_mutation,
)


def test_ccbd_state_mutation_guard_yields_schema_version_and_owner(tmp_path: Path) -> None:
    lock_path = tmp_path / "state-mutation.lock"

    with guarded_state_mutation(lock_path, "registry", owner="test") as guard:
        assert guard == StateMutationGuard(
            schema_version=CCBD_STATE_MUTATION_GUARD_SCHEMA_VERSION,
            lock_path=lock_path,
            mutation_class="registry",
            owner="test",
        )
        assert lock_path.exists()


def test_ccbd_state_mutation_guard_raises_without_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "state-mutation.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with lock_path.open("w", encoding="utf-8") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(StateMutationGuardError, match="state mutation lock unavailable"):
            with guarded_state_mutation(lock_path, "mount_lease", owner="contender"):
                raise AssertionError("guard should not enter while lock is held")


def test_ccbd_state_mutation_guard_allowlist_covers_mount_registry_and_jsonl() -> None:
    assert GUARDED_MUTATION_CLASSES == ("mount_lease", "registry", "jsonl_global_state")


def test_bridge_lock_pattern_is_registered_as_allowed_provider_lock() -> None:
    assert (
        "lib/provider_backends/codex/launcher_runtime/bridge.py:bridge.lock"
        in ALLOWED_PROVIDER_LOCK_PATTERNS
    )
