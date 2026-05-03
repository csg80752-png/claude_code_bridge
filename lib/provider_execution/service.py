from __future__ import annotations

from ccbd.api_models import JobRecord
from completion.models import CompletionDecision

from fault_injection import FaultInjectionService
from .base import ProviderRuntimeContext, ProviderSubmission
from .registry import ProviderExecutionRegistry
from .state_store import ExecutionStateStore
from .state_validation import EXECUTION_STATUS_ORPHAN, classify_execution
from .service_state import ExecutionServiceRuntimeState, ExecutionServiceStateMixin
from .service_runtime import (
    ExecutionRestoreResult,
    ExecutionUpdate,
    acknowledge,
    acknowledge_item,
    persist_submission,
    poll_updates,
    restore_submission,
)


class ExecutionService(ExecutionServiceStateMixin):
    def __init__(
        self,
        registry: ProviderExecutionRegistry,
        *,
        clock,
        state_store: ExecutionStateStore | None = None,
        fault_injection: FaultInjectionService | None = None,
    ) -> None:
        self._runtime_state = ExecutionServiceRuntimeState(
            registry=registry,
            clock=clock,
            state_store=state_store,
            fault_injection=fault_injection,
            active={},
            runtime_contexts={},
            pending_replays={},
        )

    def start(self, job: JobRecord, *, runtime_context: ProviderRuntimeContext | None = None) -> ProviderSubmission | None:
        now = self._clock()
        if self._fault_injection is not None:
            injected = self._fault_injection.consume_for_job(job, now=now)
            if injected is not None:
                items, decision = self._fault_injection.build_terminal_replay(job, injected)
                self._runtime_contexts[job.job_id] = runtime_context
                self._pending_replays[job.job_id] = (items, decision)
                return None
        adapter = self._registry.get(job.provider)
        if adapter is None:
            return None
        submission = adapter.start(job, context=runtime_context, now=now)
        self._active[job.job_id] = submission
        self._runtime_contexts[job.job_id] = runtime_context
        self._persist(job.job_id)
        return submission

    def cancel(self, job_id: str) -> None:
        self._active.pop(job_id, None)
        self._runtime_contexts.pop(job_id, None)
        self._pending_replays.pop(job_id, None)
        if self._state_store is not None:
            self._state_store.remove(job_id)

    def finish(self, job_id: str) -> None:
        self._active.pop(job_id, None)
        self._runtime_contexts.pop(job_id, None)
        self._pending_replays.pop(job_id, None)
        if self._state_store is not None:
            self._state_store.remove(job_id)

    def acknowledge(self, job_id: str) -> None:
        acknowledge(self, job_id)

    def acknowledge_item(self, job_id: str, *, event_seq: int | None) -> None:
        acknowledge_item(self, job_id, event_seq=event_seq)

    def restore(self, job: JobRecord, *, runtime_context: ProviderRuntimeContext | None = None) -> ExecutionRestoreResult:
        return restore_submission(self, job, runtime_context=runtime_context)

    def abandon_orphan_persisted_states(
        self,
        *,
        active_job_ids: frozenset[str],
    ) -> tuple[str, ...]:
        """Remove persisted execution_state files the dispatcher no longer tracks.

        A persisted state is an orphan when its submission is ``INCOMPLETE``
        but its ``job_id`` is not in ``active_job_ids`` (the set the
        dispatcher knows about after ``restore_running_jobs``). Terminal
        submissions are left in place — they do not cause a wedge and any
        cleanup belongs to a separate sweeper. Returns the tuple of
        removed ``job_id`` values for telemetry. No-op when no state store
        is configured.

        Intended call site: ccbd boot, immediately after the dispatcher's
        own ``restore_running_jobs`` settles ``active_items``.
        """
        if self._state_store is None:
            return ()
        removed: list[str] = []
        for state in self._state_store.list_all():
            status = classify_execution(state, active_job_ids=active_job_ids)
            if status == EXECUTION_STATUS_ORPHAN:
                self._state_store.remove(state.job_id)
                removed.append(state.job_id)
        return tuple(removed)

    def poll(self) -> tuple[ExecutionUpdate, ...]:
        return poll_updates(self)

    def _persist(
        self,
        job_id: str,
        *,
        pending_items: tuple = (),
        applied_event_seqs: tuple[int, ...] = (),
        pending_decision=None,
    ) -> None:
        persist_submission(
            self,
            job_id,
            pending_decision=pending_decision,
            pending_items=pending_items,
            applied_event_seqs=applied_event_seqs,
        )


__all__ = ["ExecutionRestoreResult", "ExecutionService", "ExecutionUpdate"]
