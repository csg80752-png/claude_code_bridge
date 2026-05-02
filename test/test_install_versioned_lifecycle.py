from __future__ import annotations

import json
import os
import shlex
import subprocess
import textwrap
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO_ROOT / "install.sh"


def _write_tree(root: Path, *, marker: str) -> None:
    (root / "lib").mkdir(parents=True, exist_ok=True)
    (root / "lib" / "marker.txt").write_text(marker, encoding="utf-8")
    (root / "ccb").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    (root / "install.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")


def _run_script(install_prefix: Path, body: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "CCB_LANG": "en",
            "CODEX_INSTALL_PREFIX": str(install_prefix),
            "CODEX_BIN_DIR": str(install_prefix.parent / "bin"),
        }
    )
    command = textwrap.dedent(
        f"""
        set -euo pipefail
        source {shlex.quote(str(INSTALL_SH))}
        {body}
        """
    )
    return subprocess.run(["bash", "-lc", command], capture_output=True, text=True, env=env)


def _replace(install_prefix: Path, staging: Path) -> subprocess.CompletedProcess[str]:
    return _run_script(
        install_prefix,
        f"with_install_lock replace_install_prefix_from_staging {shlex.quote(str(staging))}\n"
        "printf 'link=%s\\n' \"$(readlink \"$INSTALL_PREFIX\")\"\n",
    )


def test_versioned_install_stages_new_version_without_touching_current_link(tmp_path: Path) -> None:
    install_prefix = tmp_path / "codex-dual"
    current = tmp_path / "codex-dual.v0"
    staging = tmp_path / "staging"
    _write_tree(current, marker="old")
    install_prefix.symlink_to(current.name)
    _write_tree(staging, marker="new")

    completed = _replace(install_prefix, staging)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert install_prefix.is_symlink()
    active_target = install_prefix.resolve()
    assert active_target.name.startswith("codex-dual.v")
    assert active_target != current
    assert (active_target / "lib" / "marker.txt").read_text(encoding="utf-8") == "new"
    assert (current / "lib" / "marker.txt").read_text(encoding="utf-8") == "old"


def test_versioned_install_atomic_symlink_swap_keeps_old_version_for_rollback(tmp_path: Path) -> None:
    install_prefix = tmp_path / "codex-dual"
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_tree(first, marker="first")
    _write_tree(second, marker="second")

    assert _replace(install_prefix, first).returncode == 0
    first_target = install_prefix.resolve()
    assert _replace(install_prefix, second).returncode == 0

    rollback = json.loads((tmp_path / ".codex-dual.state" / "rollback.json").read_text(encoding="utf-8"))
    assert Path(rollback["previous_target"]).name == first_target.name
    assert Path(rollback["new_target"]).name == install_prefix.resolve().name
    assert first_target.exists()


def test_versioned_install_rollback_restores_previous_symlink(tmp_path: Path) -> None:
    install_prefix = tmp_path / "codex-dual"
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_tree(first, marker="first")
    _write_tree(second, marker="second")
    assert _replace(install_prefix, first).returncode == 0
    first_target = install_prefix.resolve()
    assert _replace(install_prefix, second).returncode == 0

    completed = _run_script(install_prefix, "rollback_install\nprintf 'active=%s\\n' \"$(readlink \"$INSTALL_PREFIX\")\"\n")

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert install_prefix.resolve() == first_target
    assert (install_prefix / "lib" / "marker.txt").read_text(encoding="utf-8") == "first"


def test_versioned_install_migrates_existing_single_prefix_directory_to_v0(tmp_path: Path) -> None:
    install_prefix = tmp_path / "codex-dual"
    staging = tmp_path / "staging"
    _write_tree(install_prefix, marker="legacy")
    _write_tree(staging, marker="new")

    completed = _replace(install_prefix, staging)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert install_prefix.is_symlink()
    assert (tmp_path / "codex-dual.v0" / "lib" / "marker.txt").read_text(encoding="utf-8") == "legacy"
    assert (install_prefix / "lib" / "marker.txt").read_text(encoding="utf-8") == "new"


def test_versioned_install_repeated_runs_allocate_unique_version_ids(tmp_path: Path) -> None:
    install_prefix = tmp_path / "codex-dual"
    allocated: list[str] = []
    for idx in range(3):
        staging = tmp_path / f"staging-{idx}"
        _write_tree(staging, marker=str(idx))
        completed = _replace(install_prefix, staging)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        allocated.append(install_prefix.resolve().name)

    assert len(set(allocated)) == 3


def test_versioned_install_preexisting_version_collision_allocates_next_id(tmp_path: Path) -> None:
    install_prefix = tmp_path / "codex-dual"
    staging = tmp_path / "staging"
    _write_tree(tmp_path / "codex-dual.v7", marker="occupied")
    _write_tree(staging, marker="new")

    completed = _run_script(
        install_prefix,
        "export CCB_INSTALL_VERSION_ID=7\n"
        f"with_install_lock replace_install_prefix_from_staging {shlex.quote(str(staging))}\n"
        "printf 'target=%s\\n' \"$(readlink \"$INSTALL_PREFIX\")\"\n",
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert install_prefix.resolve().name == "codex-dual.v8"
    rollback = json.loads((tmp_path / ".codex-dual.state" / "rollback.json").read_text(encoding="utf-8"))
    assert rollback["requested_version_id"] == 7
    assert rollback["allocated_version_id"] == 8


def test_versioned_install_commits_rollback_metadata_before_symlink_swap(tmp_path: Path) -> None:
    install_prefix = tmp_path / "codex-dual"
    staging = tmp_path / "staging"
    _write_tree(staging, marker="new")

    completed = _replace(install_prefix, staging)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    rollback = tmp_path / ".codex-dual.state" / "rollback.json"
    assert rollback.exists()
    assert "new_target" in rollback.read_text(encoding="utf-8")


def test_list_divergence_exits_nonzero_on_staging_drift(tmp_path: Path) -> None:
    install_prefix = tmp_path / "codex-dual"
    staging = tmp_path / "staging"
    drift = tmp_path / "drift"
    _write_tree(install_prefix, marker="current")
    _write_tree(staging, marker="current")
    _write_tree(drift, marker="drift")
    assert _run_script(install_prefix, "seed_install_manifest").returncode == 0

    completed = _run_script(install_prefix, f"list_install_divergence {shlex.quote(str(drift))}\n")

    assert completed.returncode == 1
    assert "staging_drift" in completed.stdout


def test_list_divergence_without_staging_dir_checks_current_install_only(tmp_path: Path) -> None:
    install_prefix = tmp_path / "codex-dual"
    _write_tree(install_prefix, marker="current")
    assert _run_script(install_prefix, "seed_install_manifest").returncode == 0

    completed = _run_script(install_prefix, "list_install_divergence\n")

    assert completed.returncode == 0
    assert "install_manifest_guard_ok action=list-divergence" in completed.stdout


@pytest.mark.parametrize("token", ["CCB_INSTALL_OVERWRITE_PATCHES", "CCB_INSTALL_ACCEPT_CURRENT_DIVERGENCE"])
def test_usage_documents_install_divergence_override_env_vars(token: str) -> None:
    assert token in INSTALL_SH.read_text(encoding="utf-8")
