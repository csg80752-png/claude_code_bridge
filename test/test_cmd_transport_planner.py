from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ccbd.services.dispatcher_runtime.reply_delivery_runtime import cmd_body_store
from ccbd.services.dispatcher_runtime.reply_delivery_runtime.cmd_transport_planner import (
    CMD_HEADER_RE,
    CmdDeliveryFallback,
    CmdDeliveryMode,
    CmdDeliveryModeResult,
    CmdDeliveryPlan,
    header_only_enabled,
    plan_cmd_delivery,
    resolve_cmd_delivery_mode,
)


def _make_reply(*, body: str = 'done', reply_id: str = 'rep-1', heartbeat: bool = False):
    diagnostics: dict = {}
    if heartbeat:
        diagnostics['notice_kind'] = 'heartbeat'
    return SimpleNamespace(
        attempt_id='att-1',
        agent_name='agent2',
        reply_id=reply_id,
        terminal_status=SimpleNamespace(value='succeeded'),
        diagnostics=diagnostics,
        reply=body,
    )


def _make_dispatcher(*, job_id: str = 'job_1234abcd'):
    source_job = SimpleNamespace(job_id=job_id, request=SimpleNamespace(task_id='task-9'))
    return SimpleNamespace(
        _message_bureau_control=SimpleNamespace(
            _attempt_store=SimpleNamespace(get_latest=lambda attempt_id: SimpleNamespace(job_id=job_id))
        ),
        get_job=lambda jid: source_job,
    )


@pytest.fixture
def _clean_env(monkeypatch):
    monkeypatch.delenv('CCB_CMD_DELIVERY_MODE', raising=False)
    monkeypatch.delenv('CCB_HEADER_ONLY', raising=False)
    monkeypatch.delenv('CCB_CMD_HEADER_ONLY_COMPATIBLE', raising=False)


def test_default_beta_gate_returns_full_body(tmp_path: Path, _clean_env) -> None:
    reply = _make_reply(body='short')
    plan, fallback = plan_cmd_delivery(_make_dispatcher(), reply, project_root=tmp_path, body_store=cmd_body_store)
    assert isinstance(plan, CmdDeliveryPlan)
    assert plan.header_only is False
    assert plan.body_file is None
    assert 'short' in plan.body
    assert fallback is None


def test_explicit_header_only_uses_single_line_header_for_long_body(monkeypatch, tmp_path: Path) -> None:
    long_body = 'x' * 2000
    reply = _make_reply(body=long_body, reply_id='rep-long-1')
    plan, fallback = plan_cmd_delivery(
        _make_dispatcher(),
        reply,
        project_root=tmp_path,
        body_store=cmd_body_store,
        delivery_mode_result=CmdDeliveryModeResult(CmdDeliveryMode.HEADER_ONLY, 'test', header_only_compatible=True),
    )
    assert plan.header_only is True
    assert plan.body_file is None
    assert CMD_HEADER_RE.fullmatch(plan.body)
    assert '\n' not in plan.body
    assert long_body not in plan.body
    assert fallback is None


def test_heartbeat_always_full_even_in_header_mode(monkeypatch, tmp_path: Path) -> None:
    long_body = 'z' * 2000
    reply = _make_reply(body=long_body, reply_id='rep-hb-1', heartbeat=True)
    plan, fallback = plan_cmd_delivery(
        _make_dispatcher(),
        reply,
        project_root=tmp_path,
        body_store=cmd_body_store,
        delivery_mode_result=CmdDeliveryModeResult(CmdDeliveryMode.HEADER_ONLY, 'test', header_only_compatible=True),
    )
    assert plan.header_only is False
    assert plan.body_file is None
    assert long_body in plan.body
    assert fallback is None


def test_source_job_id_included_when_valid(monkeypatch, tmp_path: Path) -> None:
    reply = _make_reply(body='body', reply_id='rep-job-1')
    plan, _ = plan_cmd_delivery(
        _make_dispatcher(job_id='job_12345678'),
        reply,
        project_root=tmp_path,
        body_store=cmd_body_store,
        delivery_mode_result=CmdDeliveryModeResult(CmdDeliveryMode.HEADER_ONLY, 'test', header_only_compatible=True),
    )
    assert 'job=job_12345678' in plan.body
    assert 'from=agent2' in plan.body


def test_header_only_enabled_default_is_false(_clean_env) -> None:
    assert header_only_enabled() is False
    assert resolve_cmd_delivery_mode(project_root=None).mode is CmdDeliveryMode.FULL_BODY


@pytest.mark.parametrize('value', ['0', 'false', 'no', 'off'])
def test_legacy_header_only_falsy_env_disables(monkeypatch, value: str) -> None:
    monkeypatch.delenv('CCB_CMD_DELIVERY_MODE', raising=False)
    monkeypatch.setenv('CCB_HEADER_ONLY', value)
    assert header_only_enabled() is False


@pytest.mark.parametrize('value', ['1', 'true', 'yes', 'on'])
def test_legacy_header_only_truthy_env_enables(monkeypatch, value: str) -> None:
    monkeypatch.delenv('CCB_CMD_DELIVERY_MODE', raising=False)
    monkeypatch.setenv('CCB_HEADER_ONLY', value)
    monkeypatch.setenv('CCB_CMD_HEADER_ONLY_COMPATIBLE', '1')
    assert header_only_enabled() is True


def test_legacy_header_only_malformed_fails_closed(monkeypatch) -> None:
    monkeypatch.delenv('CCB_CMD_DELIVERY_MODE', raising=False)
    monkeypatch.setenv('CCB_HEADER_ONLY', 'anything-else')
    assert header_only_enabled() is False


def test_fallback_dataclass_is_frozen() -> None:
    fb = CmdDeliveryFallback(reason='compatibility', body_char_count=42)
    with pytest.raises(AttributeError):
        fb.reason = 'other'  # type: ignore[misc]
