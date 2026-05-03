from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from ccbd.services.dispatcher_runtime.reply_delivery_runtime import preparation_service
from ccbd.services.dispatcher_runtime.reply_delivery_runtime.preparation_service import (
    _maybe_emit_cmd_pane_discovery_diag,
    reset_cmd_pane_diag_for_test,
)


@pytest.fixture(autouse=True)
def _reset_diag_gate() -> None:
    reset_cmd_pane_diag_for_test()
    yield
    reset_cmd_pane_diag_for_test()


def _make_dispatcher(
    *,
    project_root: Path | None,
    cached_pane_id: str | None = None,
    cached_age_offset: float = 0.0,
    pending_count: int = 0,
):
    layout = SimpleNamespace(project_root=project_root) if project_root is not None else None
    cache = None
    if cached_pane_id is not None:
        import time as _time
        cache = (cached_pane_id, _time.monotonic() - cached_age_offset)

    pending_events = tuple(SimpleNamespace() for _ in range(pending_count))
    kernel = SimpleNamespace(pending_events=lambda *_a, **_k: pending_events)
    control = SimpleNamespace(_mailbox_kernel=kernel)
    return SimpleNamespace(
        _layout=layout,
        _cmd_pane_cache=cache,
        _message_bureau_control=control,
    )


def _write_startup_report(project_root: Path, content: dict) -> Path:
    path = project_root / '.ccb' / 'ccbd' / 'startup-report.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content), encoding='utf-8')
    return path


def test_emits_warning_first_call(monkeypatch, tmp_path, caplog):
    _write_startup_report(tmp_path, {"bootstrap_cmd_pane": "%2"})
    dispatcher = _make_dispatcher(
        project_root=tmp_path,
        cached_pane_id="%5",
        cached_age_offset=2.5,
        pending_count=8,
    )

    monkeypatch.setattr(preparation_service, "_lookup_cmd_pane_id", lambda d, l: "%9")
    backend = SimpleNamespace(is_alive=lambda pane_id: pane_id == "%9")
    monkeypatch.setattr(preparation_service, "_get_tmux_backend", lambda d: backend)

    with caplog.at_level(logging.WARNING):
        _maybe_emit_cmd_pane_discovery_diag(dispatcher)

    msg = next(r.getMessage() for r in caplog.records if "v8.4-diag cmd-pane-discovery" in r.getMessage())
    assert "cached_pane_id='%5'" in msg
    assert "fresh_pane_id='%9'" in msg
    assert "is_alive_cached=False" in msg
    assert "is_alive_fresh=True" in msg
    assert "bootstrap_cmd_pane='%2'" in msg
    assert "pending_cmd_replies=8" in msg
    assert "supervisor_overwritten" in msg


def test_one_shot_per_lifetime(monkeypatch, tmp_path, caplog):
    dispatcher = _make_dispatcher(project_root=tmp_path)
    monkeypatch.setattr(preparation_service, "_lookup_cmd_pane_id", lambda d, l: None)
    monkeypatch.setattr(preparation_service, "_get_tmux_backend", lambda d: None)

    with caplog.at_level(logging.WARNING):
        _maybe_emit_cmd_pane_discovery_diag(dispatcher)
        _maybe_emit_cmd_pane_discovery_diag(dispatcher)
        _maybe_emit_cmd_pane_discovery_diag(dispatcher)

    records = [r for r in caplog.records if "v8.4-diag cmd-pane-discovery" in r.getMessage()]
    assert len(records) == 1


def test_handles_missing_layout(monkeypatch, caplog):
    dispatcher = SimpleNamespace(
        _layout=None,
        _cmd_pane_cache=None,
        _message_bureau_control=None,
    )
    monkeypatch.setattr(preparation_service, "_lookup_cmd_pane_id", lambda d, l: None)
    monkeypatch.setattr(preparation_service, "_get_tmux_backend", lambda d: None)

    with caplog.at_level(logging.WARNING):
        _maybe_emit_cmd_pane_discovery_diag(dispatcher)

    msg = next(r.getMessage() for r in caplog.records if "v8.4-diag cmd-pane-discovery" in r.getMessage())
    assert "cached_pane_id=None" in msg
    assert "fresh_pane_id=None" in msg
    assert "bootstrap_cmd_pane=None" in msg
    assert "pending_cmd_replies=-1" in msg


def test_handles_missing_startup_report(monkeypatch, tmp_path, caplog):
    dispatcher = _make_dispatcher(project_root=tmp_path, cached_pane_id="%2", pending_count=0)
    monkeypatch.setattr(preparation_service, "_lookup_cmd_pane_id", lambda d, l: "%2")
    backend = SimpleNamespace(is_alive=lambda pane_id: True)
    monkeypatch.setattr(preparation_service, "_get_tmux_backend", lambda d: backend)

    with caplog.at_level(logging.WARNING):
        _maybe_emit_cmd_pane_discovery_diag(dispatcher)

    msg = next(r.getMessage() for r in caplog.records if "v8.4-diag cmd-pane-discovery" in r.getMessage())
    assert "bootstrap_cmd_pane=None" in msg
    expected_path = str(tmp_path / '.ccb' / 'ccbd' / 'startup-report.json')
    assert f"bootstrap_report_path={expected_path}" in msg


def test_falls_back_to_actions_taken(monkeypatch, tmp_path, caplog):
    _write_startup_report(tmp_path, {"actions_taken": ["foo:bar", "bootstrap_cmd_pane:%4", "baz:qux"]})
    dispatcher = _make_dispatcher(project_root=tmp_path)
    monkeypatch.setattr(preparation_service, "_lookup_cmd_pane_id", lambda d, l: "%4")
    monkeypatch.setattr(preparation_service, "_get_tmux_backend", lambda d: None)

    with caplog.at_level(logging.WARNING):
        _maybe_emit_cmd_pane_discovery_diag(dispatcher)

    msg = next(r.getMessage() for r in caplog.records if "v8.4-diag cmd-pane-discovery" in r.getMessage())
    assert "bootstrap_cmd_pane='%4'" in msg


def test_handles_lookup_exception(monkeypatch, tmp_path, caplog):
    dispatcher = _make_dispatcher(project_root=tmp_path, cached_pane_id="%2")

    def _raise(d, l):
        raise RuntimeError("tmux fail")

    monkeypatch.setattr(preparation_service, "_lookup_cmd_pane_id", _raise)
    monkeypatch.setattr(preparation_service, "_get_tmux_backend", lambda d: None)

    with caplog.at_level(logging.WARNING):
        _maybe_emit_cmd_pane_discovery_diag(dispatcher)

    msg = next(r.getMessage() for r in caplog.records if "v8.4-diag cmd-pane-discovery" in r.getMessage())
    assert "fresh_pane_id=None" in msg


def test_handles_is_alive_exception(monkeypatch, tmp_path, caplog):
    dispatcher = _make_dispatcher(project_root=tmp_path, cached_pane_id="%2")
    monkeypatch.setattr(preparation_service, "_lookup_cmd_pane_id", lambda d, l: "%2")

    def _raise(pane_id):
        raise RuntimeError("dead pane")

    backend = SimpleNamespace(is_alive=_raise)
    monkeypatch.setattr(preparation_service, "_get_tmux_backend", lambda d: backend)

    with caplog.at_level(logging.WARNING):
        _maybe_emit_cmd_pane_discovery_diag(dispatcher)

    msg = next(r.getMessage() for r in caplog.records if "v8.4-diag cmd-pane-discovery" in r.getMessage())
    assert "is_alive_cached=None" in msg


def test_handles_invalid_startup_report_json(monkeypatch, tmp_path, caplog):
    path = tmp_path / '.ccb' / 'ccbd' / 'startup-report.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not-json{", encoding='utf-8')
    dispatcher = _make_dispatcher(project_root=tmp_path)
    monkeypatch.setattr(preparation_service, "_lookup_cmd_pane_id", lambda d, l: None)
    monkeypatch.setattr(preparation_service, "_get_tmux_backend", lambda d: None)

    with caplog.at_level(logging.WARNING):
        _maybe_emit_cmd_pane_discovery_diag(dispatcher)

    msg = next(r.getMessage() for r in caplog.records if "v8.4-diag cmd-pane-discovery" in r.getMessage())
    assert "bootstrap_cmd_pane=None" in msg


def test_pending_kernel_exception(monkeypatch, tmp_path, caplog):
    layout = SimpleNamespace(project_root=tmp_path)

    def _raise(*a, **k):
        raise RuntimeError("kernel down")

    kernel = SimpleNamespace(pending_events=_raise)
    control = SimpleNamespace(_mailbox_kernel=kernel)
    dispatcher = SimpleNamespace(
        _layout=layout,
        _cmd_pane_cache=None,
        _message_bureau_control=control,
    )

    monkeypatch.setattr(preparation_service, "_lookup_cmd_pane_id", lambda d, l: None)
    monkeypatch.setattr(preparation_service, "_get_tmux_backend", lambda d: None)

    with caplog.at_level(logging.WARNING):
        _maybe_emit_cmd_pane_discovery_diag(dispatcher)

    msg = next(r.getMessage() for r in caplog.records if "v8.4-diag cmd-pane-discovery" in r.getMessage())
    assert "pending_cmd_replies=-1" in msg
