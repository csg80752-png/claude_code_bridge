from __future__ import annotations

from pathlib import Path

from ccbd.api_models import JobRecord
from provider_core.protocol import request_anchor_for_job
from completion.models import CompletionConfidence, CompletionStatus
from provider_execution.base import ProviderPollResult, ProviderRuntimeContext, ProviderSubmission
from provider_execution.common import request_anchor_from_runtime_state
from terminal_runtime import get_backend_for_session

from .comm import ClaudeLogReader
from .execution_runtime import poll_submission as _poll_submission
from .execution_runtime import looks_ready
from .execution_runtime import resume_submission as _resume_submission
from .execution_runtime import start_active_submission as _start_active_submission
from .session import load_project_session


class ClaudeProviderAdapter:
    provider = 'claude'

    def start(self, job: JobRecord, *, context: ProviderRuntimeContext | None, now: str) -> ProviderSubmission:
        return _start_active_submission(
            self,
            job,
            context=context,
            now=now,
            load_session_fn=_load_session,
            backend_for_session_fn=get_backend_for_session,
            reader_factory=_reader_factory,
            request_anchor_fn=request_anchor_for_job,
        )

    def poll(self, submission: ProviderSubmission, *, now: str) -> ProviderPollResult | None:
        return _poll_submission(self, submission, now=now)

    def export_runtime_state(self, submission: ProviderSubmission) -> dict[str, object]:
        return {
            'mode': submission.runtime_state.get('mode'),
            'state': submission.runtime_state.get('state') or {},
            'pane_id': submission.runtime_state.get('pane_id'),
            'request_anchor': request_anchor_from_runtime_state(submission.runtime_state, fallback=submission.job_id),
            'next_seq': submission.runtime_state.get('next_seq'),
            'anchor_seen': submission.runtime_state.get('anchor_seen'),
            'no_wrap': submission.runtime_state.get('no_wrap'),
            'reply_buffer': submission.runtime_state.get('reply_buffer'),
            'raw_buffer': submission.runtime_state.get('raw_buffer'),
            'session_path': submission.runtime_state.get('session_path'),
            'last_assistant_uuid': submission.runtime_state.get('last_assistant_uuid'),
            'completion_dir': submission.runtime_state.get('completion_dir'),
            'prompt_text': submission.runtime_state.get('prompt_text'),
            'prompt_sent': submission.runtime_state.get('prompt_sent'),
            'prompt_sent_at': submission.runtime_state.get('prompt_sent_at'),
            'reply_delivery_complete_on_dispatch': submission.runtime_state.get('reply_delivery_complete_on_dispatch'),
            'reply_delivery_require_ready': submission.runtime_state.get('reply_delivery_require_ready'),
            'ready_wait_started_at': submission.runtime_state.get('ready_wait_started_at'),
            'ready_timeout_s': submission.runtime_state.get('ready_timeout_s'),
        }

    def resume(
        self,
        job: JobRecord,
        submission: ProviderSubmission,
        *,
        context: ProviderRuntimeContext | None,
        persisted_state,
        now: str,
    ) -> ProviderSubmission | None:
        del persisted_state, now
        if context is None or not context.workspace_path:
            return None
        resumed = _resume_submission(
            job,
            submission,
            context=context,
            load_session_fn=_load_session,
            backend_for_session_fn=get_backend_for_session,
            reader_factory=_reader_factory,
        )
        if resumed is None:
            return None
        return _terminal_if_restart_interrupted_before_anchor(resumed) or resumed


def _reader_factory(session):
    root = _session_projects_root(session)
    kwargs = {'work_dir': Path(session.work_dir)}
    kwargs['root'] = root
    return ClaudeLogReader(**kwargs)


def _session_projects_root(session) -> Path | None:
    data = getattr(session, 'data', None)
    raw = ''
    if isinstance(data, dict):
        raw = str(data.get('claude_projects_root') or '').strip()
    if not raw:
        raw = str(getattr(session, 'claude_projects_root', '') or '').strip()
    if raw:
        return Path(raw).expanduser()
    return _legacy_session_projects_root(session)


def _legacy_session_projects_root(session) -> Path:
    data = getattr(session, 'data', None)
    runtime_raw = ''
    if isinstance(data, dict):
        runtime_raw = str(data.get('runtime_dir') or '').strip()
    if not runtime_raw:
        runtime_raw = str(getattr(session, 'runtime_dir', '') or '').strip()
    if runtime_raw:
        return Path(runtime_raw).expanduser() / 'claude-home' / '.claude' / 'projects'
    return Path(session.work_dir).expanduser() / '.ccb' / 'claude-home' / '.claude' / 'projects'


def _terminal_if_restart_interrupted_before_anchor(submission: ProviderSubmission) -> ProviderSubmission | None:
    state = submission.runtime_state
    if not bool(state.get("prompt_sent", False)):
        return None
    if bool(state.get("anchor_seen", False)):
        return None
    if str(state.get("reply_buffer") or "").strip() or str(submission.reply or "").strip():
        return None
    backend = state.get("backend")
    pane_id = str(state.get("pane_id") or "").strip()
    get_pane_content = getattr(backend, "get_pane_content", None)
    if not pane_id or not callable(get_pane_content):
        return None
    try:
        pane_text = str(get_pane_content(pane_id, lines=120) or "")
    except Exception:
        return None
    if not looks_ready(pane_text):
        return None
    return ProviderSubmission(
        job_id=submission.job_id,
        agent_name=submission.agent_name,
        provider=submission.provider,
        accepted_at=submission.accepted_at,
        ready_at=submission.ready_at,
        source_kind=submission.source_kind,
        reply="",
        status=CompletionStatus.FAILED,
        reason="interrupted_by_restart",
        confidence=CompletionConfidence.DEGRADED,
        diagnostics={
            **dict(submission.diagnostics or {}),
            "restore_status": "terminal_pending",
            "restore_reason": "interrupted_by_restart",
            "pane_id": pane_id,
        },
        runtime_state={
            **state,
            "restore_terminal_reason": "interrupted_by_restart",
        },
    )


def _load_session(work_dir: Path, *, agent_name: str):
    from .execution_runtime.start import load_session as _runtime_load_session

    return _runtime_load_session(load_project_session, work_dir, agent_name=agent_name)


def build_execution_adapter() -> ClaudeProviderAdapter:
    return ClaudeProviderAdapter()


__all__ = ['ClaudeProviderAdapter', 'build_execution_adapter']
