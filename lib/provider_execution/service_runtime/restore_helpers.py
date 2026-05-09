from __future__ import annotations

from ccbd.api_models import JobRecord
from completion.models import CompletionConfidence, CompletionDecision, CompletionItemKind, CompletionStatus

from .models import ExecutionRestoreResult
from .persistence import filter_pending_items, persist_submission


def adapter_or_result(service, job: JobRecord):
    adapter = service._registry.get(job.provider)
    if adapter is not None:
        return adapter, None
    return None, abandon_restore(
        service,
        job,
        reason='adapter_missing',
        resume_capable=False,
    )


def persisted_state_or_result(service, job: JobRecord):
    persisted = load_persisted_state(service, job)
    if persisted is None:
        return None, result(
            job,
            status='missing',
            reason='state_missing',
            resume_capable=False,
        )
    if persisted.provider == job.provider:
        return persisted, None
    return None, abandon_restore(
        service,
        job,
        reason='provider_mismatch',
        resume_capable=persisted.resume_capable,
        pending_items_count=len(persisted.pending_items),
    )


def recover_pending_items(service, job_id: str, persisted) -> tuple[list, object | None]:
    pending_items = filter_pending_items(persisted)
    if pending_items:
        service._pending_replays[job_id] = (
            pending_items,
            persisted.pending_decision,
        )
    return pending_items, persisted.pending_decision


def terminal_pending_result(job: JobRecord, persisted, pending_items: list) -> ExecutionRestoreResult | None:
    decision = persisted.pending_decision or terminal_decision_from_applied_state(persisted)
    if decision is None or pending_items:
        return None
    return terminal_pending_restore(job, persisted, decision=decision)


def resume_or_result(adapter, service, job: JobRecord, persisted, pending_items: list, restored_context):
    resume = getattr(adapter, 'resume', None)
    if not persisted.resume_capable or not callable(resume):
        return None, abandon_restore(
            service,
            job,
            reason='provider_resume_unsupported',
            resume_capable=persisted.resume_capable,
            pending_items_count=len(pending_items),
        )
    submission = resume_submission(adapter, service, job, persisted, restored_context)
    if submission is not None:
        return submission, None
    return None, abandon_restore(
        service,
        job,
        reason='provider_resume_rejected',
        resume_capable=persisted.resume_capable,
        pending_items_count=len(pending_items),
    )


def persist_restored_submission(service, job_id: str, submission, *, restored_context, persisted, pending_items: list) -> None:
    service._active[job_id] = submission
    service._runtime_contexts[job_id] = restored_context
    persist_submission(
        service,
        job_id,
        pending_decision=persisted.pending_decision,
        pending_items=pending_items,
        applied_event_seqs=persisted.applied_event_seqs,
    )


def terminal_submission_result(service, job: JobRecord, submission) -> ExecutionRestoreResult | None:
    if submission.status is CompletionStatus.INCOMPLETE:
        return None
    if not _submission_declares_restore_terminal(submission):
        return None
    reason = str(submission.reason or "provider_restore_terminal").strip() or "provider_restore_terminal"
    diagnostics = {
        "restore_status": "terminal_pending",
        "restore_reason": reason,
        "provider": submission.provider,
        **dict(submission.diagnostics or {}),
    }
    decision = CompletionDecision(
        terminal=True,
        status=submission.status,
        reason=reason,
        confidence=submission.confidence,
        reply=str(submission.reply or ""),
        anchor_seen=bool(submission.runtime_state.get("anchor_seen", False)),
        reply_started=bool(submission.runtime_state.get("reply_started", False)) or bool(submission.reply),
        reply_stable=False,
        provider_turn_ref=str(
            submission.runtime_state.get("request_anchor")
            or submission.runtime_state.get("pane_id")
            or submission.job_id
        ).strip() or submission.job_id,
        source_cursor=None,
        finished_at=service._clock(),
        diagnostics=diagnostics,
    )
    return result(
        job,
        status="terminal_pending",
        reason=reason,
        resume_capable=True,
        decision=decision,
    )


def _submission_declares_restore_terminal(submission) -> bool:
    diagnostics = dict(submission.diagnostics or {})
    if str(diagnostics.get("restore_status") or "") == "terminal_pending":
        return True
    return bool(str(submission.runtime_state.get("restore_terminal_reason") or "").strip())


def restored_result(job: JobRecord, *, pending_items: list) -> ExecutionRestoreResult:
    return result(
        job,
        status='replay_pending' if pending_items else 'restored',
        reason='pending_items_recovered' if pending_items else 'provider_resumed',
        resume_capable=True,
        pending_items_count=len(pending_items),
    )


def restore_preflight_result(service, job: JobRecord) -> ExecutionRestoreResult | None:
    if job.job_id in service._active:
        return result(
            job,
            status='restored',
            reason='already_active',
            resume_capable=True,
        )
    if service._state_store is None:
        return result(
            job,
            status='missing',
            reason='state_store_disabled',
            resume_capable=False,
        )
    return None


def load_persisted_state(service, job: JobRecord):
    return service._state_store.load(job.job_id)


def abandon_restore(
    service,
    job: JobRecord,
    *,
    reason: str,
    resume_capable: bool,
    pending_items_count: int = 0,
) -> ExecutionRestoreResult:
    service._state_store.remove(job.job_id)
    return result(
        job,
        status='abandoned',
        reason=reason,
        resume_capable=resume_capable,
        pending_items_count=pending_items_count,
    )


def terminal_pending_restore(job: JobRecord, persisted, *, decision) -> ExecutionRestoreResult:
    return result(
        job,
        status='terminal_pending',
        reason='terminal_decision_recovered' if persisted.pending_decision is not None else 'terminal_state_recovered',
        resume_capable=persisted.resume_capable,
        decision=decision,
    )


def terminal_decision_from_applied_state(persisted) -> CompletionDecision | None:
    submission = persisted.submission
    runtime_state = dict(getattr(submission, 'runtime_state', {}) or {})
    if not bool(runtime_state.get('reached_terminal')):
        return None
    terminal_evidence = _terminal_evidence_from_items(getattr(persisted, 'pending_items', ()) or ())
    reply = _recovered_terminal_reply(submission, terminal_evidence)
    if not reply:
        return None
    status = terminal_evidence[0] if terminal_evidence is not None else CompletionStatus.COMPLETED
    reason = terminal_evidence[1] if terminal_evidence is not None else 'terminal_state_recovered'
    return CompletionDecision(
        terminal=True,
        status=status,
        reason=reason,
        confidence=CompletionConfidence.OBSERVED,
        reply=reply,
        anchor_seen=bool(runtime_state.get('anchor_seen')),
        reply_started=bool(runtime_state.get('reply_started')) or bool(reply),
        reply_stable=bool(runtime_state.get('reply_stable')) or bool(runtime_state.get('reached_terminal')),
        provider_turn_ref=_provider_turn_ref(runtime_state),
        source_cursor=None,
        finished_at=str(getattr(persisted, 'persisted_at', '') or ''),
        diagnostics={
            'restore_kind': 'applied_terminal_state',
            'pending_items_count': len(getattr(persisted, 'pending_items', ()) or ()),
            'applied_event_seqs_count': len(getattr(persisted, 'applied_event_seqs', ()) or ()),
        },
    )


def _terminal_evidence_from_items(items) -> tuple[CompletionStatus, str, dict[str, object], CompletionItemKind] | None:
    for item in reversed(tuple(items)):
        kind = getattr(item, 'kind', None)
        payload = dict(getattr(item, 'payload', {}) or {})
        if kind is CompletionItemKind.TURN_ABORTED:
            return (
                _status_from_payload(payload, default=CompletionStatus.FAILED),
                _reason_from_payload(payload, 'turn_aborted'),
                payload,
                kind,
            )
        if kind is CompletionItemKind.CANCEL_INFO:
            return CompletionStatus.CANCELLED, _reason_from_payload(payload, 'cancelled'), payload, kind
        if kind is CompletionItemKind.ERROR or kind is CompletionItemKind.PANE_DEAD:
            return CompletionStatus.FAILED, _reason_from_payload(payload, 'error'), payload, kind
        if kind is CompletionItemKind.TURN_BOUNDARY or kind is CompletionItemKind.RESULT:
            return (
                _status_from_payload(payload, default=CompletionStatus.COMPLETED),
                _reason_from_payload(payload, 'task_complete'),
                payload,
                kind,
            )
    return None


def _recovered_terminal_reply(
    submission,
    terminal_evidence: tuple[CompletionStatus, str, dict[str, object], CompletionItemKind] | None,
) -> str:
    reply = str(getattr(submission, 'reply', '') or '').strip()
    if reply:
        return reply
    if terminal_evidence is None:
        return ''
    payload = terminal_evidence[2]
    kind = terminal_evidence[3]
    if kind is CompletionItemKind.TURN_ABORTED:
        fallback_keys = ('last_agent_message', 'reply')
    elif kind is CompletionItemKind.TURN_BOUNDARY or kind is CompletionItemKind.RESULT:
        fallback_keys = ('reply', 'text', 'merged_text', 'last_agent_message')
    else:
        fallback_keys = ()
    for key in fallback_keys:
        value = str(payload.get(key) or '').strip()
        if value:
            return value
    return ''


def _status_from_payload(payload: dict[str, object], *, default: CompletionStatus) -> CompletionStatus:
    raw = str(payload.get('status') or '').strip().lower()
    if raw:
        try:
            return CompletionStatus(raw)
        except ValueError:
            pass
    return default


def _reason_from_payload(payload: dict[str, object], default: str) -> str:
    return str(payload.get('reason') or default).strip() or default


def _provider_turn_ref(runtime_state: dict[str, object]) -> str | None:
    for key in ('bound_turn_id', 'provider_turn_ref', 'turn_id'):
        value = str(runtime_state.get(key) or '').strip()
        if value:
            return value
    return None


def resume_submission(adapter, service, job: JobRecord, persisted, restored_context):
    return adapter.resume(
        job,
        persisted.submission,
        context=restored_context,
        persisted_state=persisted,
        now=service._clock(),
    )


def result(
    job: JobRecord,
    *,
    status: str,
    reason: str,
    resume_capable: bool,
    pending_items_count: int = 0,
    decision=None,
) -> ExecutionRestoreResult:
    return ExecutionRestoreResult(
        job_id=job.job_id,
        agent_name=job.agent_name,
        provider=job.provider,
        status=status,
        reason=reason,
        resume_capable=resume_capable,
        pending_items_count=pending_items_count,
        decision=decision,
    )


__all__ = [
    'adapter_or_result',
    'persisted_state_or_result',
    'recover_pending_items',
    'restore_preflight_result',
    'resume_or_result',
    'persist_restored_submission',
    'restored_result',
    'terminal_submission_result',
    'terminal_pending_result',
    'terminal_decision_from_applied_state',
]
