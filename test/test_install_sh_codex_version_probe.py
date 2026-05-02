from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO_ROOT / "install.sh"


def _run_probe(script: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-lc", f"source {INSTALL_SH}; {script}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env={**os.environ, **(env or {})},
    )


def _write_executable(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_detect_codex_cli_version_prefers_env_override_without_probe(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    probe_marker = tmp_path / "called"
    _write_executable(
        bin_dir / "codex",
        f"#!/bin/sh\nprintf called > {probe_marker}\necho codex-cli from-probe\n",
    )

    completed = _run_probe(
        "PATH=$FAKE_BIN detect_codex_cli_version",
        env={
            "FAKE_BIN": str(bin_dir),
            "CCB_CODEX_CLI_VERSION": "codex-cli override-1.2.3",
        },
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "codex-cli override-1.2.3"
    assert not probe_marker.exists()


def test_detect_codex_cli_version_uses_timeout_wrapper_when_available(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    timeout_log = tmp_path / "timeout.args"
    _write_executable(
        bin_dir / "timeout",
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" > {timeout_log}\nshift\nexec \"$@\"\n",
    )
    _write_executable(
        bin_dir / "codex",
        "#!/bin/sh\n[ \"$1\" = \"--version\" ] || exit 2\necho codex-cli timeout-path\n",
    )

    completed = _run_probe(
        "PATH=$FAKE_BIN detect_codex_cli_version",
        env={"FAKE_BIN": f"{bin_dir}:/bin", "CCB_CODEX_CLI_VERSION": ""},
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "codex-cli timeout-path"
    assert timeout_log.read_text(encoding="utf-8").strip() == "5s codex --version"


def test_detect_codex_cli_version_falls_back_when_timeout_absent(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "codex",
        "#!/bin/sh\n[ \"$1\" = \"--version\" ] || exit 2\necho codex-cli fallback-path\n",
    )

    completed = _run_probe(
        "PATH=$FAKE_BIN detect_codex_cli_version",
        env={"FAKE_BIN": str(bin_dir), "CCB_CODEX_CLI_VERSION": ""},
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "codex-cli fallback-path"


def test_detect_codex_cli_version_fallback_bounds_hanging_probe(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "codex",
        "#!/bin/sh\n[ \"$1\" = \"--version\" ] || exit 2\n/bin/sleep 10\necho too-late\n",
    )

    started = time.monotonic()
    completed = _run_probe(
        "PATH=$FAKE_BIN detect_codex_cli_version",
        env={"FAKE_BIN": str(bin_dir), "CCB_CODEX_CLI_VERSION": ""},
    )
    elapsed = time.monotonic() - started

    assert completed.returncode == 0
    assert completed.stdout.strip() == ""
    assert elapsed < 7
