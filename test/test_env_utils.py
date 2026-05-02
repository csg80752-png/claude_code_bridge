from __future__ import annotations

import os

from runtime_env import env_bool, env_default_on, env_float, env_int


def test_env_bool_truthy_and_falsy(monkeypatch) -> None:
    monkeypatch.delenv("X", raising=False)
    assert env_bool("X", default=True) is True
    assert env_bool("X", default=False) is False

    for v in ("1", "true", "yes", "on", " TRUE ", "Yes"):
        monkeypatch.setenv("X", v)
        assert env_bool("X", default=False) is True

    for v in ("0", "false", "no", "off", " 0 ", "False"):
        monkeypatch.setenv("X", v)
        assert env_bool("X", default=True) is False

    monkeypatch.setenv("X", "maybe")
    assert env_bool("X", default=True) is True
    assert env_bool("X", default=False) is False


def test_env_int_parsing(monkeypatch) -> None:
    monkeypatch.delenv("X", raising=False)
    assert env_int("X", 7) == 7

    monkeypatch.setenv("X", " 42 ")
    assert env_int("X", 7) == 42

    monkeypatch.setenv("X", "bad")
    assert env_int("X", 7) == 7


def test_env_default_on_matrix(monkeypatch) -> None:
    # Production-default kill switch: True unless explicitly disabled.
    monkeypatch.delenv("X", raising=False)
    assert env_default_on("X") is True

    for v in ("", "1", "true", "yes", "on", " TRUE ", "Yes", "maybe", "xyz"):
        monkeypatch.setenv("X", v)
        assert env_default_on("X") is True, f"unset/truthy/unknown should be True, got False for {v!r}"

    for v in ("0", "false", "no", "off", " 0 ", "False", "OFF", "No"):
        monkeypatch.setenv("X", v)
        assert env_default_on("X") is False, f"falsy rollback token should be False, got True for {v!r}"


def test_env_float_parsing(monkeypatch) -> None:
    monkeypatch.delenv("X", raising=False)
    assert env_float("X", 1.5) == 1.5

    monkeypatch.setenv("X", " 2.75 ")
    assert env_float("X", 1.5) == 2.75

    monkeypatch.setenv("X", "bad")
    assert env_float("X", 1.5) == 1.5
