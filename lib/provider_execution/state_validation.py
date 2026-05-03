from __future__ import annotations

from completion.models import CompletionStatus

from .state_models import PersistedExecutionState

EXECUTION_STATUS_INACTIVE = 'inactive'
EXECUTION_STATUS_ORPHAN = 'orphan'
EXECUTION_STATUS_ACTIVE = 'active'


def classify_execution(
    state: PersistedExecutionState,
    *,
    active_job_ids: frozenset[str],
) -> str:
    """Classify a persisted execution state without mutating storage.

    Returns one of:

    - ``'inactive'`` — the persisted submission has reached a terminal status
      (``COMPLETED`` / ``CANCELLED`` / ``FAILED``). The execution is finished;
      the file is stale bookkeeping but does not cause a wedge.
    - ``'orphan'`` — the persisted submission is ``INCOMPLETE`` but the
      dispatcher no longer tracks the job (it dropped out of ``active_items``
      between the previous ccbd life and now). The execution will never be
      resumed; the file should be removed so it does not accumulate or
      surface in summaries as recoverable.
    - ``'active'`` — the persisted submission is ``INCOMPLETE`` and the
      dispatcher still tracks the job. ``restore_running_jobs`` already
      attempted to resume it.

    The function is pure; it has no side effects. Callers (typically the
    boot path in ``ccbd.app_runtime.lifecycle``) decide when to remove an
    orphan via ``ExecutionStateStore.remove``.
    """
    if state.submission.status is not CompletionStatus.INCOMPLETE:
        return EXECUTION_STATUS_INACTIVE
    if state.job_id in active_job_ids:
        return EXECUTION_STATUS_ACTIVE
    return EXECUTION_STATUS_ORPHAN


__all__ = [
    'EXECUTION_STATUS_ACTIVE',
    'EXECUTION_STATUS_INACTIVE',
    'EXECUTION_STATUS_ORPHAN',
    'classify_execution',
]
