from __future__ import annotations

import os
import shlex
import subprocess
import textwrap
from pathlib import Path

import pytest

from cli.management_runtime.source_restore import (
    SourceOnlyDisposition,
    classify_source_restore,
    restore_source_tree,
    seed_install_manifest,
)
from cli.management_runtime import source_restore


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO_ROOT / "install.sh"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _manifest_entries(manifest_path: Path) -> list[str]:
    return sorted(line.split(None, 1)[1] for line in manifest_path.read_text(encoding="utf-8").splitlines())


def _run_install_guard_snippet(install_prefix: Path, snippet: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update({"CCB_LANG": "en", "CODEX_INSTALL_PREFIX": str(install_prefix)})
    command = textwrap.dedent(
        f"""
        set -euo pipefail
        source {shlex.quote(str(INSTALL_SH))}
        {snippet}
        """
    )
    return subprocess.run(
        ["bash", "-lc", command],
        capture_output=True,
        text=True,
        env=env,
    )


def test_source_restore_manifest_matches_restored_file_set(tmp_path: Path) -> None:
    live = tmp_path / "live"
    source = tmp_path / "source"
    _write(live / "kept.txt", "live-current\n")
    _write(live / "missing.txt", "live-only\n")
    _write(live / "nested" / "data.txt", "nested-live\n")
    _write(source / "kept.txt", "stale-source\n")
    _write(source / "old.txt", "deleted-from-live\n")

    result = restore_source_tree(
        live,
        source,
        source_only_dispositions={
            "old.txt": SourceOnlyDisposition(action="delete", reason="removed from live before v8.3 seed")
        },
    )
    manifest = seed_install_manifest(source)

    assert result.diff.modified == ("kept.txt",)
    assert result.diff.missing_from_source == ("missing.txt", "nested/data.txt")
    assert result.diff.source_only == ("old.txt",)
    assert (source / "kept.txt").read_text(encoding="utf-8") == "live-current\n"
    assert (source / "missing.txt").read_text(encoding="utf-8") == "live-only\n"
    assert not (source / "old.txt").exists()
    assert manifest.file_count == 3
    assert _manifest_entries(manifest.path) == ["kept.txt", "missing.txt", "nested/data.txt"]


def test_source_restore_handles_source_only_stale_files_before_manifest_seed(tmp_path: Path) -> None:
    live = tmp_path / "live"
    source = tmp_path / "source"
    _write(live / "kept.txt", "same\n")
    _write(source / "kept.txt", "same\n")
    _write(source / "stale.txt", "source-only\n")

    diff = classify_source_restore(live, source)

    assert diff.missing_from_source == ()
    assert diff.modified == ()
    assert diff.source_only == ("stale.txt",)
    with pytest.raises(RuntimeError, match="unresolved source-only"):
        restore_source_tree(live, source)


def test_source_restore_deleted_in_live_kept_in_source_is_removed_or_quarantined_before_seed(tmp_path: Path) -> None:
    live = tmp_path / "live"
    source = tmp_path / "source"
    quarantine = tmp_path / "quarantine"
    _write(live / "kept.txt", "same\n")
    _write(source / "kept.txt", "same\n")
    _write(source / "stale.txt", "source-only\n")

    result = restore_source_tree(
        live,
        source,
        source_only_dispositions={
            "stale.txt": SourceOnlyDisposition(
                action="quarantine",
                reason="deleted in live; preserve for audit",
                quarantine_root=quarantine,
            )
        },
    )
    manifest = seed_install_manifest(source)

    assert result.source_only_resolutions["stale.txt"].action == "quarantine"
    assert not (source / "stale.txt").exists()
    assert (quarantine / "stale.txt").read_text(encoding="utf-8") == "source-only\n"
    assert _manifest_entries(manifest.path) == ["kept.txt"]


def test_source_restore_manifest_seed_runs_before_future_install(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write(source / "ccb", "#!/usr/bin/env bash\n")
    _write(source / "lib" / "module.py", "VALUE = 1\n")

    manifest = seed_install_manifest(source)

    assert manifest.path == source / ".ccb-install-manifest.sha256"
    assert manifest.file_count == 2
    assert all(".ccb-install-manifest" not in entry for entry in _manifest_entries(manifest.path))


def test_source_side_install_without_manifest_fails_close_before_mutation(tmp_path: Path) -> None:
    source_prefix = tmp_path / "source-prefix"
    _write(source_prefix / "ccb", "#!/usr/bin/env bash\n")

    completed = _run_install_guard_snippet(source_prefix, 'guard_install_prefix_current "install_all"')

    assert completed.returncode == 2
    assert "missing_manifest:" in completed.stdout
    assert "install_manifest_guard_ok" not in completed.stdout


def test_source_side_install_with_preseeded_manifest_enters_guard_flow(tmp_path: Path) -> None:
    source_prefix = tmp_path / "source-prefix"
    _write(source_prefix / "ccb", "#!/usr/bin/env bash\n")

    seed = _run_install_guard_snippet(source_prefix, "seed_install_manifest")
    guard = _run_install_guard_snippet(source_prefix, 'guard_install_prefix_current "install_all"')

    assert seed.returncode == 0
    assert "seeded_install_manifest:" in seed.stdout
    assert guard.returncode == 0
    assert "install_manifest_guard_ok action=install_all" in guard.stdout


def test_copy_entry_preserves_existing_destination_when_file_copy_fails(monkeypatch, tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    _write(src, "new\n")
    _write(dst, "old\n")

    def fail_copy(source: Path, target: Path) -> None:
        Path(target).write_text("partial\n", encoding="utf-8")
        raise RuntimeError("copy failed after partial write")

    monkeypatch.setattr(source_restore.shutil, "copy2", fail_copy)

    try:
        source_restore._copy_entry(src, dst)
    except RuntimeError as exc:
        assert "copy failed" in str(exc)
    else:
        raise AssertionError("_copy_entry must propagate copy failure")

    assert dst.read_text(encoding="utf-8") == "old\n"
    assert not list(tmp_path.glob(".dst.txt.tmp.*"))
