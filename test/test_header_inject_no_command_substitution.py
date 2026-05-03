from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from ccbd.services.dispatcher_runtime.reply_delivery_runtime.cmd_transport_planner import prepare_cmd_payload


@pytest.mark.parametrize("shell", ["bash", "sh", "zsh"])
def test_header_inject_no_command_substitution_in_shells(shell: str, tmp_path: Path) -> None:
    binary = shutil.which(shell)
    if binary is None:
        pytest.skip(f"{shell} not installed")
    marker = tmp_path / "executed"
    header = prepare_cmd_payload(
        sender_id="agent1",
        body_bytes=123,
        source_job_id="job_1234abcd",
    )

    result = subprocess.run(
        [binary, "-c", f"{header}\ntest -e {marker}"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert not marker.exists()


def test_header_inject_no_command_substitution_in_node_repl(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    marker = tmp_path / "executed"
    header = prepare_cmd_payload(
        sender_id="agent1",
        body_bytes=123,
        source_job_id="job_1234abcd",
    )

    result = subprocess.run(
        [node],
        input=f"{header}\n",
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )

    assert result.returncode != 0
    assert not marker.exists()
