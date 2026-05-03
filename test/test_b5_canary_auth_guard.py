from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from provider_backends.codex.launcher_runtime.turn_id_probe import BROKEN_STATE
from test_b5_canary_suite import (
    _skip_if_codex_auth_missing,
    _skip_if_codex_probe_auth_error,
)


def test_b5_codex_turn_id_probe_skips_when_auth_file_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    with pytest.raises(pytest.skip.Exception, match="B5 canary requires authenticated codex-cli"):
        _skip_if_codex_auth_missing()


def test_b5_codex_turn_id_probe_uses_codex_home_auth_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("HOME", str(tmp_path / "unauth-home"))

    _skip_if_codex_auth_missing()


def test_b5_codex_turn_id_probe_skips_when_probe_reports_auth_error() -> None:
    result = SimpleNamespace(
        state=BROKEN_STATE,
        error="401 Unauthorized: Missing bearer or basic authentication",
    )

    with pytest.raises(pytest.skip.Exception, match="B5 canary requires authenticated codex-cli"):
        _skip_if_codex_probe_auth_error(result)
