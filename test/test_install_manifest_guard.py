from __future__ import annotations

import os
import shlex
import subprocess
import textwrap
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
LIVE_INSTALL_SH = REPO_ROOT / "install.sh"
SOURCE_INSTALL_SH = Path("/home/speed/projects/claude_code_bridge/install.sh")
INSTALLERS = [LIVE_INSTALL_SH, SOURCE_INSTALL_SH]


def _write_tree(root: Path, *, lib_text: str = "patched\n", test_text: str = "test\n") -> None:
    (root / "lib" / "pkg").mkdir(parents=True, exist_ok=True)
    (root / "test").mkdir(parents=True, exist_ok=True)
    (root / "lib" / "pkg" / "module.py").write_text(lib_text, encoding="utf-8")
    (root / "test" / "test_module.py").write_text(test_text, encoding="utf-8")
    (root / "install.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (root / "ccb").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "guide.md").write_text("guide\n", encoding="utf-8")


def _run_script(
    script: Path,
    *,
    install_prefix: Path,
    body: str,
    overwrite: bool = False,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "CCB_LANG": "en",
            "CODEX_INSTALL_PREFIX": str(install_prefix),
            "CODEX_BIN_DIR": str(install_prefix.parent / "bin"),
        }
    )
    if overwrite:
        env["CCB_INSTALL_OVERWRITE_PATCHES"] = "1"
    if extra_env:
        env.update(extra_env)
    command = textwrap.dedent(
        f"""
        set -euo pipefail
        source {shlex.quote(str(script))}
        {body}
        """
    )
    return subprocess.run(
        ["bash", "-lc", command],
        capture_output=True,
        text=True,
        env=env,
    )


def _seed_manifest(script: Path, install_prefix: Path) -> subprocess.CompletedProcess[str]:
    return _run_script(
        script,
        install_prefix=install_prefix,
        body="seed_install_manifest",
    )


def _guard(
    script: Path,
    *,
    install_prefix: Path,
    staging: Path | None,
    action: str = "copy_project",
    overwrite: bool = False,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    staging_arg = "" if staging is None else str(staging)
    return _run_script(
        script,
        install_prefix=install_prefix,
        overwrite=overwrite,
        extra_env=extra_env,
        body=(
            "guard_install_prefix_wipe "
            f"{shlex.quote(staging_arg)} {shlex.quote(action)}\n"
            "echo guard-passed"
        ),
    )


@pytest.mark.parametrize("script", INSTALLERS, ids=lambda path: path.parent.name)
def test_seed_manifest_records_whole_prefix_files_and_ignores_caches(tmp_path: Path, script: Path) -> None:
    install_prefix = tmp_path / "install"
    _write_tree(install_prefix)
    cache_file = install_prefix / "lib" / "pkg" / "__pycache__" / "module.cpython-312.pyc"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_bytes(b"cache")

    completed = _seed_manifest(script, install_prefix)

    assert completed.returncode == 0, completed.stderr + completed.stdout
    manifest = install_prefix / ".ccb-install-manifest.sha256"
    text = manifest.read_text(encoding="utf-8")
    assert "lib/pkg/module.py" in text
    assert "test/test_module.py" in text
    assert "install.sh" in text
    assert "ccb" in text
    assert "docs/guide.md" in text
    assert "__pycache__" not in text
    assert ".pyc" not in text


@pytest.mark.parametrize("script", INSTALLERS, ids=lambda path: path.parent.name)
def test_seed_manifest_excludes_runtime_generated_live_shape(tmp_path: Path, script: Path) -> None:
    install_prefix = tmp_path / "install"
    staging = tmp_path / "staging"
    _write_tree(install_prefix)
    _write_tree(staging)
    runtime_files = [
        install_prefix / ".ccb" / "agents" / "agent1" / "runtime.json",
        install_prefix / ".ccb" / "ccbd" / "state.json",
        install_prefix / ".ccb" / "history" / "agent1.jsonl",
        install_prefix / ".ccb" / "metrics" / "body_read_followup.jsonl",
        install_prefix / ".ccb" / "replies" / "reply-1.json",
        install_prefix / ".ccb" / ".codex-agent1-session",
        install_prefix / ".ccb" / ".claude-agent3-session",
        install_prefix / ".codex",
        install_prefix / ".gemini" / "settings.json",
        install_prefix / ".loop" / "autonomous-loop-state.json",
        install_prefix / ".commit-checklist.md",
        install_prefix / "lib" / "provider_backends" / "claude" / "launcher.py.bak-r2-restore",
    ]
    for path in runtime_files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("runtime\n", encoding="utf-8")

    completed = _seed_manifest(script, install_prefix)

    assert completed.returncode == 0, completed.stderr + completed.stdout
    text = (install_prefix / ".ccb-install-manifest.sha256").read_text(encoding="utf-8")
    assert ".ccb/agents" not in text
    assert ".ccb/ccbd" not in text
    assert ".ccb/history" not in text
    assert ".ccb/metrics" not in text
    assert ".ccb/replies" not in text
    assert ".ccb/.codex-agent1-session" not in text
    assert ".ccb/.claude-agent3-session" not in text
    assert ".codex" not in text
    assert ".gemini" not in text
    assert ".loop" not in text
    assert ".commit-checklist.md" not in text
    assert "bak-r2-restore" not in text
    assert _guard(script, install_prefix=install_prefix, staging=staging).returncode == 0


@pytest.mark.parametrize("script", INSTALLERS, ids=lambda path: path.parent.name)
def test_installer_has_single_install_tree_exclusion_definition(script: Path) -> None:
    text = script.read_text(encoding="utf-8")
    assert text.count("INSTALL_TREE_EXCLUDE_PATTERNS=(") == 1
    assert "CCB_INSTALL_TREE_EXCLUDES" in text
    assert text.count("local exclude_args=()") == 1
    assert text.count("for pattern in \"${INSTALL_TREE_EXCLUDE_PATTERNS[@]}\"") == 1


@pytest.mark.parametrize("script", INSTALLERS, ids=lambda path: path.parent.name)
def test_guard_aborts_non_empty_install_without_manifest(tmp_path: Path, script: Path) -> None:
    install_prefix = tmp_path / "install"
    staging = tmp_path / "staging"
    _write_tree(install_prefix)
    _write_tree(staging)

    completed = _guard(script, install_prefix=install_prefix, staging=staging)

    assert completed.returncode == 2
    output = completed.stdout + completed.stderr
    assert "missing_manifest" in output
    assert "guard-passed" not in output


@pytest.mark.parametrize("script", INSTALLERS, ids=lambda path: path.parent.name)
def test_guard_allows_seeded_install_when_current_and_staging_match(
    tmp_path: Path,
    script: Path,
) -> None:
    install_prefix = tmp_path / "install"
    staging = tmp_path / "staging"
    _write_tree(install_prefix)
    _write_tree(staging)
    assert _seed_manifest(script, install_prefix).returncode == 0

    completed = _guard(script, install_prefix=install_prefix, staging=staging)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "guard-passed" in completed.stdout


@pytest.mark.parametrize("script", INSTALLERS, ids=lambda path: path.parent.name)
def test_guard_aborts_modified_current_file_even_when_staging_has_same_path(
    tmp_path: Path,
    script: Path,
) -> None:
    install_prefix = tmp_path / "install"
    staging = tmp_path / "staging"
    _write_tree(install_prefix, lib_text="patched\n")
    _write_tree(staging, lib_text="patched\n")
    assert _seed_manifest(script, install_prefix).returncode == 0
    (install_prefix / "lib" / "pkg" / "module.py").write_text("local edit\n", encoding="utf-8")

    completed = _guard(script, install_prefix=install_prefix, staging=staging)

    assert completed.returncode == 2
    output = completed.stdout + completed.stderr
    assert "current_divergence" in output
    assert "lib/pkg/module.py" in output
    assert "guard-passed" not in output


@pytest.mark.parametrize("script", INSTALLERS, ids=lambda path: path.parent.name)
def test_guard_aborts_seeded_manifest_when_staging_would_replace_same_path_with_drift(
    tmp_path: Path,
    script: Path,
) -> None:
    install_prefix = tmp_path / "install"
    staging = tmp_path / "staging"
    _write_tree(install_prefix, lib_text="patched live\n")
    _write_tree(staging, lib_text="unpatched source\n")
    assert _seed_manifest(script, install_prefix).returncode == 0

    completed = _guard(script, install_prefix=install_prefix, staging=staging)

    assert completed.returncode == 2
    output = completed.stdout + completed.stderr
    assert "staging_drift" in output
    assert "lib/pkg/module.py" in output


@pytest.mark.parametrize("script", INSTALLERS, ids=lambda path: path.parent.name)
def test_guard_aborts_seeded_manifest_when_staging_omits_manifest_path(
    tmp_path: Path,
    script: Path,
) -> None:
    install_prefix = tmp_path / "install"
    staging = tmp_path / "staging"
    _write_tree(install_prefix)
    _write_tree(staging)
    assert _seed_manifest(script, install_prefix).returncode == 0
    (staging / "docs" / "guide.md").unlink()

    completed = _guard(script, install_prefix=install_prefix, staging=staging)

    assert completed.returncode == 2
    output = completed.stdout + completed.stderr
    assert "staging_drift" in output
    assert "docs/guide.md" in output
    assert "guard-passed" not in output


@pytest.mark.parametrize("script", INSTALLERS, ids=lambda path: path.parent.name)
def test_guard_aborts_seeded_manifest_when_staging_adds_new_scoped_file(
    tmp_path: Path,
    script: Path,
) -> None:
    install_prefix = tmp_path / "install"
    staging = tmp_path / "staging"
    _write_tree(install_prefix)
    _write_tree(staging)
    assert _seed_manifest(script, install_prefix).returncode == 0
    (staging / "lib" / "pkg" / "new_upstream.py").write_text("new\n", encoding="utf-8")

    completed = _guard(script, install_prefix=install_prefix, staging=staging)

    assert completed.returncode == 2
    output = completed.stdout + completed.stderr
    assert "staging_drift" in output
    assert "new_upstream.py" in output
    assert "guard-passed" not in output


def test_source_installer_blocks_seeded_manifest_source_drift(tmp_path: Path) -> None:
    install_prefix = tmp_path / "install"
    staging = tmp_path / "staging"
    _write_tree(install_prefix, lib_text="patched live\n")
    _write_tree(staging, lib_text="unpatched source\n")
    assert _seed_manifest(SOURCE_INSTALL_SH, install_prefix).returncode == 0

    completed = _guard(SOURCE_INSTALL_SH, install_prefix=install_prefix, staging=staging)

    assert completed.returncode == 2
    output = completed.stdout + completed.stderr
    assert "staging_drift" in output
    assert "lib/pkg/module.py" in output


@pytest.mark.parametrize("script", INSTALLERS, ids=lambda path: path.parent.name)
def test_guard_aborts_local_only_scoped_file(tmp_path: Path, script: Path) -> None:
    install_prefix = tmp_path / "install"
    staging = tmp_path / "staging"
    _write_tree(install_prefix)
    _write_tree(staging)
    assert _seed_manifest(script, install_prefix).returncode == 0
    (install_prefix / "lib" / "pkg" / "local_only.py").write_text("local\n", encoding="utf-8")

    completed = _guard(script, install_prefix=install_prefix, staging=staging)

    assert completed.returncode == 2
    output = completed.stdout + completed.stderr
    assert "current_divergence" in output
    assert "local_only.py" in output


@pytest.mark.parametrize("script", INSTALLERS, ids=lambda path: path.parent.name)
def test_guard_ignores_pycache_churn(tmp_path: Path, script: Path) -> None:
    install_prefix = tmp_path / "install"
    staging = tmp_path / "staging"
    _write_tree(install_prefix)
    _write_tree(staging)
    assert _seed_manifest(script, install_prefix).returncode == 0
    cache_file = install_prefix / "lib" / "pkg" / "__pycache__" / "module.cpython-312.pyc"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_bytes(b"cache")

    completed = _guard(script, install_prefix=install_prefix, staging=staging)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "guard-passed" in completed.stdout


@pytest.mark.parametrize("script", INSTALLERS, ids=lambda path: path.parent.name)
def test_uninstall_guard_aborts_modified_file(tmp_path: Path, script: Path) -> None:
    install_prefix = tmp_path / "install"
    _write_tree(install_prefix)
    assert _seed_manifest(script, install_prefix).returncode == 0
    (install_prefix / "lib" / "pkg" / "module.py").write_text("local edit\n", encoding="utf-8")

    completed = _guard(script, install_prefix=install_prefix, staging=None, action="uninstall_all")

    assert completed.returncode == 2
    output = completed.stdout + completed.stderr
    assert "current_divergence" in output
    assert "lib/pkg/module.py" in output


@pytest.mark.parametrize("script", INSTALLERS, ids=lambda path: path.parent.name)
def test_overwrite_escape_hatch_permits_guard_after_reporting_drift(
    tmp_path: Path,
    script: Path,
) -> None:
    install_prefix = tmp_path / "install"
    staging = tmp_path / "staging"
    _write_tree(install_prefix, lib_text="patched live\n")
    _write_tree(staging, lib_text="unpatched source\n")
    assert _seed_manifest(script, install_prefix).returncode == 0

    completed = _guard(
        script,
        install_prefix=install_prefix,
        staging=staging,
        overwrite=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    output = completed.stdout + completed.stderr
    assert "staging_drift" in output
    assert "CCB_INSTALL_OVERWRITE_PATCHES=1" in output
    assert "guard-passed" in completed.stdout


@pytest.mark.parametrize("script", INSTALLERS, ids=lambda path: path.parent.name)
def test_overwrite_escape_hatch_does_not_permit_current_divergence(
    tmp_path: Path,
    script: Path,
) -> None:
    install_prefix = tmp_path / "install"
    staging = tmp_path / "staging"
    _write_tree(install_prefix)
    _write_tree(staging)
    assert _seed_manifest(script, install_prefix).returncode == 0
    (install_prefix / "lib" / "pkg" / "module.py").write_text("local edit\n", encoding="utf-8")

    completed = _guard(script, install_prefix=install_prefix, staging=staging, overwrite=True)

    assert completed.returncode == 2
    output = completed.stdout + completed.stderr
    assert "current_divergence" in output
    assert "CCB_INSTALL_OVERWRITE_PATCHES=1" not in output
    assert "guard-passed" not in output


@pytest.mark.parametrize("script", INSTALLERS, ids=lambda path: path.parent.name)
def test_accept_current_divergence_escape_hatch_is_separate(
    tmp_path: Path,
    script: Path,
) -> None:
    install_prefix = tmp_path / "install"
    staging = tmp_path / "staging"
    _write_tree(install_prefix)
    _write_tree(staging)
    assert _seed_manifest(script, install_prefix).returncode == 0
    (install_prefix / "lib" / "pkg" / "module.py").write_text("local edit\n", encoding="utf-8")

    completed = _guard(
        script,
        install_prefix=install_prefix,
        staging=staging,
        extra_env={"CCB_INSTALL_ACCEPT_CURRENT_DIVERGENCE": "1"},
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    output = completed.stdout + completed.stderr
    assert "current_divergence" in output
    assert "CCB_INSTALL_ACCEPT_CURRENT_DIVERGENCE=1" in output
    assert "guard-passed" in completed.stdout


@pytest.mark.parametrize("script", INSTALLERS, ids=lambda path: path.parent.name)
def test_with_install_lock_fails_closed_when_flock_missing(tmp_path: Path, script: Path) -> None:
    completed = _run_script(
        script,
        install_prefix=tmp_path / "install",
        extra_env={"CCB_TEST_FORCE_NO_FLOCK": "1"},
        body="with_install_lock echo should-not-run",
    )

    assert completed.returncode == 2
    output = completed.stdout + completed.stderr
    assert "flock unavailable" in output
    assert "should-not-run" not in output


@pytest.mark.parametrize("script", INSTALLERS, ids=lambda path: path.parent.name)
def test_no_flock_opt_out_allows_explicit_unlocked_operation(tmp_path: Path, script: Path) -> None:
    completed = subprocess.run(
        [
            "bash",
            str(script),
            "--no-flock-i-accept-races",
            "seed-install-manifest",
        ],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "CCB_LANG": "en",
            "CODEX_INSTALL_PREFIX": str(tmp_path / "install"),
            "CODEX_BIN_DIR": str(tmp_path / "bin"),
            "CCB_TEST_FORCE_NO_FLOCK": "1",
        },
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "seeded_install_manifest" in completed.stdout


@pytest.mark.parametrize("script", INSTALLERS, ids=lambda path: path.parent.name)
def test_install_all_seeds_manifest_after_post_copy_prefix_mutations(tmp_path: Path, script: Path) -> None:
    install_prefix = tmp_path / "install"
    body = """
    require_major_upgrade_confirmation() { :; }
    install_requirements() { :; }
    remove_codex_mcp() { :; }
    cleanup_legacy_files() { :; }
    copy_project() { mkdir -p "$INSTALL_PREFIX"; printf 'copied\\n' > "$INSTALL_PREFIX/ccb"; }
    write_install_metadata() { printf '9.9.9\\n' > "$INSTALL_PREFIX/VERSION"; printf '{}\\n' > "$INSTALL_PREFIX/BUILD_INFO.json"; }
    install_bin_links() { :; }
    ensure_path_configured() { :; }
    install_claude_commands() { :; }
    install_claude_skills() { :; }
    install_codex_skills() { :; }
    install_droid_skills() { :; }
    install_droid_delegation() { :; }
    install_claude_md_config() { :; }
    install_agents_md_config() { printf 'agents\\n' > "$INSTALL_PREFIX/AGENTS.md"; }
    install_clinerules_config() { printf 'rules\\n' > "$INSTALL_PREFIX/.clinerules"; }
    install_settings_permissions() { :; }
    install_tmux_config() { :; }
    print_install_identity_summary() { :; }
    install_all
    """

    completed = _run_script(script, install_prefix=install_prefix, body=body)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    manifest = (install_prefix / ".ccb-install-manifest.sha256").read_text(encoding="utf-8")
    assert "VERSION" in manifest
    assert "BUILD_INFO.json" in manifest
    assert "AGENTS.md" in manifest
    assert ".clinerules" in manifest


@pytest.mark.parametrize("script", INSTALLERS, ids=lambda path: path.parent.name)
def test_install_all_guard_runs_before_legacy_cleanup(tmp_path: Path, script: Path) -> None:
    install_prefix = tmp_path / "install"
    legacy = install_prefix / "bin" / "caskd"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("legacy\n", encoding="utf-8")
    body = """
    cleanup_legacy_files() { rm -f "$INSTALL_PREFIX/bin/caskd"; echo cleanup-ran; }
    copy_project() { echo copy-ran; }
    install_all_locked
    """

    completed = _run_script(script, install_prefix=install_prefix, body=body)

    assert completed.returncode == 2
    output = completed.stdout + completed.stderr
    assert "missing_manifest" in output
    assert "cleanup-ran" not in output
    assert "copy-ran" not in output
    assert legacy.exists()


@pytest.mark.parametrize("script", INSTALLERS, ids=lambda path: path.parent.name)
def test_copy_project_does_not_seed_manifest_before_install_finalize(script: Path) -> None:
    text = script.read_text(encoding="utf-8")
    start = text.index("\ncopy_project() {")
    end = text.index("\n}\n\ninstall_bin_links()", start)
    copy_project_body = text[start:end]
    assert "seed_install_manifest" not in copy_project_body
