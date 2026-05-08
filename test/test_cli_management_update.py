from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from cli.management_runtime.commands_runtime import update as update_runtime


def test_cmd_update_defaults_to_latest_release(monkeypatch, tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    tmp_base = tmp_path / "tmp-base"
    tmp_base.mkdir()
    captured: dict[str, object] = {}

    monkeypatch.setenv("CODEX_INSTALL_PREFIX", str(install_dir))
    monkeypatch.setattr(update_runtime.platform, "system", lambda: "Linux")
    monkeypatch.setattr(update_runtime, "pick_temp_base_dir", lambda _install_dir: tmp_base)
    monkeypatch.setattr(update_runtime, "get_available_versions", lambda: ["5.1.0", "5.3.0", "5.2.8"])

    def _fake_update_via_tarball(tmp_base_arg, *, install_dir, target_version, old_info):
        captured["tmp_base"] = tmp_base_arg
        captured["install_dir"] = install_dir
        captured["target_version"] = target_version
        captured["old_info"] = old_info
        return 0

    monkeypatch.setattr(update_runtime, "_update_via_tarball", _fake_update_via_tarball)

    code = update_runtime.cmd_update(SimpleNamespace(target=None), script_root=tmp_path / "script-root")

    assert code == 0
    assert captured["tmp_base"] == tmp_base
    assert captured["install_dir"] == install_dir
    assert captured["target_version"] == "5.3.0"


def test_cmd_update_errors_when_latest_release_cannot_be_resolved(monkeypatch, tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    tmp_base = tmp_path / "tmp-base"
    tmp_base.mkdir()

    monkeypatch.setenv("CODEX_INSTALL_PREFIX", str(install_dir))
    monkeypatch.setattr(update_runtime.platform, "system", lambda: "Linux")
    monkeypatch.setattr(update_runtime, "pick_temp_base_dir", lambda _install_dir: tmp_base)
    monkeypatch.setattr(update_runtime, "get_available_versions", lambda: [])

    code = update_runtime.cmd_update(SimpleNamespace(target=None), script_root=tmp_path / "script-root")

    assert code == 1


def test_cmd_update_rejects_non_linux_platform(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr(update_runtime.platform, "system", lambda: "Darwin")

    code = update_runtime.cmd_update(SimpleNamespace(target=None), script_root=tmp_path / "script-root")

    assert code == 1
    captured = capsys.readouterr()
    assert "Linux/WSL" in captured.out


def test_release_artifact_name_uses_linux_arch_aliases(monkeypatch) -> None:
    monkeypatch.setattr(update_runtime.platform, "machine", lambda: "amd64")
    assert update_runtime._release_artifact_name() == "ccb-linux-x86_64.tar.gz"

    monkeypatch.setattr(update_runtime.platform, "machine", lambda: "arm64")
    assert update_runtime._release_artifact_name() == "ccb-linux-aarch64.tar.gz"


def test_release_artifact_url_points_to_release_download() -> None:
    url = update_runtime._release_artifact_url("6.0.0", artifact_name="ccb-linux-x86_64.tar.gz")

    assert url == "https://github.com/bfly123/claude_code_bridge/releases/download/v6.0.0/ccb-linux-x86_64.tar.gz"


def test_update_via_tarball_falls_back_to_github_source_archive(monkeypatch, tmp_path: Path) -> None:
    tmp_base = tmp_path / "tmp"
    install_dir = tmp_path / "install"
    downloaded_urls: list[str] = []
    installed: dict[str, object] = {}

    def fake_download(url: str, destination: Path) -> bool:
        downloaded_urls.append(url)
        if url.endswith("ccb-linux-x86_64.tar.gz"):
            return False
        destination.write_bytes(b"archive")
        return True

    def fake_extract(tar, destination: Path) -> None:
        del tar
        extracted = destination / "claude_code_bridge-6.0.29"
        extracted.mkdir(parents=True)
        (extracted / "install.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    def fake_run(command, *, check, env):
        installed["command"] = command
        installed["check"] = check
        installed["env"] = env

    monkeypatch.setattr(update_runtime.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(update_runtime, "download_tarball", fake_download)
    monkeypatch.setattr(update_runtime, "safe_extract_tar", fake_extract)
    monkeypatch.setattr(update_runtime.tarfile, "open", lambda *args, **kwargs: _NullTar())
    monkeypatch.setattr(update_runtime.shutil, "which", lambda name: "/bin/bash" if name == "bash" else None)
    monkeypatch.setattr(update_runtime.subprocess, "run", fake_run)
    monkeypatch.setattr(update_runtime, "get_version_info", lambda _install_dir: {"version": "6.0.29"})

    code = update_runtime._update_via_tarball(
        tmp_base,
        install_dir=install_dir,
        target_version="6.0.29",
        old_info={"version": "6.0.28"},
    )

    assert code == 0
    assert downloaded_urls == [
        "https://github.com/bfly123/claude_code_bridge/releases/download/v6.0.29/ccb-linux-x86_64.tar.gz",
        "https://github.com/bfly123/claude_code_bridge/archive/refs/tags/v6.0.29.tar.gz",
    ]
    assert installed["command"] == ["/bin/bash", str(tmp_base / "ccb_update" / "claude_code_bridge-6.0.29" / "install.sh"), "install"]
    assert installed["env"]["CODEX_INSTALL_PREFIX"] == str(install_dir)


def test_update_via_tarball_rejects_source_archive_version_mismatch(monkeypatch, tmp_path: Path, capsys) -> None:
    tmp_base = tmp_path / "tmp"
    install_dir = tmp_path / "install"
    run_calls: list[list[str]] = []

    def fake_download(url: str, destination: Path) -> bool:
        if url.endswith("ccb-linux-x86_64.tar.gz"):
            return False
        destination.write_bytes(b"archive")
        return True

    def fake_extract(tar, destination: Path) -> None:
        del tar
        extracted = destination / "claude_code_bridge-6.0.29"
        extracted.mkdir(parents=True)
        (extracted / "install.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    monkeypatch.setattr(update_runtime.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(update_runtime, "download_tarball", fake_download)
    monkeypatch.setattr(update_runtime, "safe_extract_tar", fake_extract)
    monkeypatch.setattr(update_runtime.tarfile, "open", lambda *args, **kwargs: _NullTar())
    monkeypatch.setattr(update_runtime.shutil, "which", lambda name: "/bin/bash" if name == "bash" else None)
    monkeypatch.setattr(update_runtime.subprocess, "run", lambda command, *, check, env: run_calls.append(command))
    monkeypatch.setattr(update_runtime, "get_version_info", lambda _install_dir: {"version": "6.0.28"})

    code = update_runtime._update_via_tarball(
        tmp_base,
        install_dir=install_dir,
        target_version="6.0.29",
        old_info={"version": "6.0.28"},
    )

    assert code == 1
    assert run_calls == [
        ["/bin/bash", str(tmp_base / "ccb_update" / "claude_code_bridge-6.0.29" / "install.sh"), "install"],
        ["/bin/bash", str(tmp_base / "ccb_update" / "claude_code_bridge-6.0.29" / "install.sh"), "rollback-install"],
    ]
    assert "installed version 6.0.28 does not match requested v6.0.29" in capsys.readouterr().out


def test_release_extract_dir_name_strips_tar_suffixes() -> None:
    assert update_runtime._release_extract_dir_name("ccb-linux-x86_64.tar.gz") == "ccb-linux-x86_64"
    assert update_runtime._release_extract_dir_name("ccb-linux-aarch64.tgz") == "ccb-linux-aarch64"
    assert update_runtime._release_extract_dir_name("ccb-preview.zip") == "ccb-preview"


class _NullTar:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False
