from __future__ import annotations

from pathlib import Path

from ccbd.api_models import JobRecord
from provider_core.protocol import request_anchor_for_job, wrap_codex_turn_prompt
from provider_execution.base import ProviderPollResult, ProviderRuntimeContext, ProviderSubmission
from terminal_runtime import get_backend_for_session

from .comm import CodexLogReader
from .comm_runtime.follow_gate import workspace_follow_enabled
from .comm_runtime.paths import SESSION_ROOT
from .execution_runtime import poll_submission as _poll_submission
from .execution_runtime import resume_submission as _resume_submission
from .execution_runtime import start_active_submission as _start_active_submission
from .execution_runtime.state_machine_runtime.serialization import to_runtime_state, from_runtime_state
from .session import load_project_session


class CodexProviderAdapter:
    provider = 'codex'

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
            wrap_prompt_fn=wrap_codex_turn_prompt,
        )

    def poll(self, submission: ProviderSubmission, *, now: str) -> ProviderPollResult | None:
        return _poll_submission(submission, now=now)

    def export_runtime_state(self, submission: ProviderSubmission) -> dict[str, object]:
        poll_state = from_runtime_state(submission.runtime_state, fallback_request_anchor=submission.job_id)
        return {
            'mode': submission.runtime_state.get('mode'),
            'state': submission.runtime_state.get('state') or {},
            'pane_id': submission.runtime_state.get('pane_id'),
            'no_wrap': submission.runtime_state.get('no_wrap'),
            **to_runtime_state(poll_state),
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
        return _resume_submission(
            job,
            submission,
            context=context,
            load_session_fn=_load_session,
            backend_for_session_fn=get_backend_for_session,
            reader_factory=_reader_factory,
        )


def _reader_factory(session, preferred_log: Path | None):
    root = SESSION_ROOT
    isolated = False
    own_session_file = getattr(session, 'session_file', None)
    try:
        from provider_backends.codex.launcher_runtime.codex_namespace_isolation import (
            codex_runtime_dir_from_session_file,
            resolve_codex_sessions_root,
        )
        from provider_profiles.materializer import load_resolved_provider_profile

        runtime_dir = codex_runtime_dir_from_session_file(own_session_file)
        if runtime_dir is not None:
            resolved = resolve_codex_sessions_root(runtime_dir, profile=load_resolved_provider_profile(runtime_dir))
            root = resolved.path
            isolated = resolved.is_isolated
    except Exception:
        root = SESSION_ROOT
        isolated = False
    return CodexLogReader(
        root=root,
        log_path=preferred_log if preferred_log is not None else (Path(session.codex_session_path).expanduser() if session.codex_session_path else None),
        session_id_filter=session.codex_session_id or None,
        work_dir=Path(session.work_dir),
        follow_workspace_sessions=workspace_follow_enabled(),
        isolated_to_root=isolated,
        own_session_file=own_session_file,
    )


def _load_session(work_dir: Path, agent_name: str):
    from .execution_runtime.start import load_session as _runtime_load_session

    return _runtime_load_session(load_project_session, work_dir, agent_name=agent_name)


def build_execution_adapter() -> CodexProviderAdapter:
    return CodexProviderAdapter()


__all__ = ['CodexProviderAdapter', 'build_execution_adapter']
