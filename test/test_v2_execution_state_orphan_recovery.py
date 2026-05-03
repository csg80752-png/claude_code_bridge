from __future__ import annotations

from pathlib import Path

from completion.models import (
    CompletionConfidence,
    CompletionSourceKind,
    CompletionStatus,
)
from provider_execution.base import ProviderRuntimeContext, ProviderSubmission
from provider_execution.state_models import PersistedExecutionState
from provider_execution.state_store import ExecutionStateStore
from provider_execution.state_validation import (
    EXECUTION_STATUS_ACTIVE,
    EXECUTION_STATUS_INACTIVE,
    EXECUTION_STATUS_ORPHAN,
    classify_execution,
)
from storage.paths import PathLayout


def _make_state(
    *,
    job_id: str,
    status: CompletionStatus = CompletionStatus.INCOMPLETE,
    persisted_at: str = '2026-04-30T16:51:17Z',
    runtime_state: dict | None = None,
) -> PersistedExecutionState:
    submission = ProviderSubmission(
        job_id=job_id,
        agent_name='agent1',
        provider='codex',
        accepted_at='2026-04-30T16:51:00Z',
        ready_at='2026-04-30T16:51:01Z',
        source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
        reply='',
        status=status,
        reason='in_progress' if status is CompletionStatus.INCOMPLETE else 'finished',
        confidence=CompletionConfidence.OBSERVED,
        diagnostics=None,
        runtime_state=runtime_state or {'mode': 'active'},
    )
    runtime_context = ProviderRuntimeContext(
        agent_name='agent1',
        workspace_path='/tmp/repo',
        backend_type='codex',
        runtime_ref=None,
        session_ref=None,
    )
    return PersistedExecutionState(
        submission=submission,
        runtime_context=runtime_context,
        resume_capable=False,
        persisted_at=persisted_at,
    )


def _make_store(tmp_path: Path) -> ExecutionStateStore:
    layout = PathLayout(tmp_path / 'repo')
    return ExecutionStateStore(layout)


# --- pure classifier --------------------------------------------------------


def test_classify_incomplete_unknown_to_dispatcher_is_orphan() -> None:
    state = _make_state(job_id='job_orphan_1', status=CompletionStatus.INCOMPLETE)
    assert classify_execution(state, active_job_ids=frozenset()) == EXECUTION_STATUS_ORPHAN


def test_classify_incomplete_known_to_dispatcher_is_active() -> None:
    state = _make_state(job_id='job_active_1', status=CompletionStatus.INCOMPLETE)
    assert (
        classify_execution(state, active_job_ids=frozenset({'job_active_1'}))
        == EXECUTION_STATUS_ACTIVE
    )


def test_classify_terminal_status_is_inactive_regardless_of_dispatcher() -> None:
    for terminal in (CompletionStatus.COMPLETED, CompletionStatus.CANCELLED, CompletionStatus.FAILED):
        state = _make_state(job_id='job_terminal_' + terminal.value, status=terminal)
        assert classify_execution(state, active_job_ids=frozenset()) == EXECUTION_STATUS_INACTIVE
        assert (
            classify_execution(state, active_job_ids=frozenset({'job_terminal_' + terminal.value}))
            == EXECUTION_STATUS_INACTIVE
        )


def test_classify_contaminated_runtime_state_does_not_change_outcome() -> None:
    """The classifier ignores provider-specific runtime_state flags and uses
    only submission.status + dispatcher tracking. bound_turn_contaminated is
    a codex symptom of the wedge, not a classification axis.
    """
    contaminated = _make_state(
        job_id='job_contaminated',
        status=CompletionStatus.INCOMPLETE,
        runtime_state={'mode': 'active', 'bound_turn_contaminated': True, 'requires_turn_id': True},
    )
    clean = _make_state(
        job_id='job_clean',
        status=CompletionStatus.INCOMPLETE,
        runtime_state={'mode': 'active'},
    )
    empty = frozenset()
    assert classify_execution(contaminated, active_job_ids=empty) == EXECUTION_STATUS_ORPHAN
    assert classify_execution(clean, active_job_ids=empty) == EXECUTION_STATUS_ORPHAN


# --- store-level abandonment ------------------------------------------------


def test_abandon_orphan_removes_only_unknown_incomplete(tmp_path: Path) -> None:
    from provider_execution.registry import build_default_execution_registry
    from provider_execution.service import ExecutionService

    store = _make_store(tmp_path)
    orphan = _make_state(job_id='job_orphan')
    active = _make_state(job_id='job_active')
    terminal = _make_state(job_id='job_done', status=CompletionStatus.COMPLETED)
    for state in (orphan, active, terminal):
        store.save(state)

    service = ExecutionService(
        build_default_execution_registry(),
        clock=lambda: '2026-05-04T03:00:00Z',
        state_store=store,
    )

    removed = service.abandon_orphan_persisted_states(
        active_job_ids=frozenset({'job_active'})
    )

    assert removed == ('job_orphan',)
    remaining = {state.job_id for state in store.list_all()}
    assert remaining == {'job_active', 'job_done'}


def test_abandon_orphan_no_state_store_returns_empty() -> None:
    from provider_execution.registry import build_default_execution_registry
    from provider_execution.service import ExecutionService

    service = ExecutionService(
        build_default_execution_registry(),
        clock=lambda: '2026-05-04T03:00:00Z',
        state_store=None,
    )

    assert service.abandon_orphan_persisted_states(active_job_ids=frozenset()) == ()


def test_abandon_orphan_recovers_real_world_stale_executions(tmp_path: Path) -> None:
    """Mirror of the canary state: 7 stale executions on disk after an
    abnormal terminate, none of them in the dispatcher's active set after
    restore. All should be cleared in a single sweep.
    """
    from provider_execution.registry import build_default_execution_registry
    from provider_execution.service import ExecutionService

    store = _make_store(tmp_path)
    job_ids = (
        'job_08d9561d5d12',
        'job_0bf121459cd2',
        'job_401059ac1bc3',
        'job_410d0a95c186',
        'job_4687b45b8209',
        'job_51efce9be8aa',
        'job_e8fa626c5fa4',
    )
    for job_id in job_ids:
        store.save(_make_state(job_id=job_id))

    service = ExecutionService(
        build_default_execution_registry(),
        clock=lambda: '2026-05-04T03:00:00Z',
        state_store=store,
    )

    removed = service.abandon_orphan_persisted_states(active_job_ids=frozenset())

    assert sorted(removed) == sorted(job_ids)
    assert store.list_all() == []


def test_abandon_orphan_idempotent_on_second_pass(tmp_path: Path) -> None:
    from provider_execution.registry import build_default_execution_registry
    from provider_execution.service import ExecutionService

    store = _make_store(tmp_path)
    store.save(_make_state(job_id='job_orphan'))
    store.save(_make_state(job_id='job_active'))
    service = ExecutionService(
        build_default_execution_registry(),
        clock=lambda: '2026-05-04T03:00:00Z',
        state_store=store,
    )

    first = service.abandon_orphan_persisted_states(
        active_job_ids=frozenset({'job_active'})
    )
    second = service.abandon_orphan_persisted_states(
        active_job_ids=frozenset({'job_active'})
    )

    assert first == ('job_orphan',)
    assert second == ()
    assert {state.job_id for state in store.list_all()} == {'job_active'}


def test_abandon_orphan_summary_field_count_matches_removed(tmp_path: Path) -> None:
    """The lifecycle helper exposes ``orphan_executions_removed`` to the
    startup report. Verify the count is the length of the removed tuple.
    """
    from provider_execution.registry import build_default_execution_registry
    from provider_execution.service import ExecutionService

    store = _make_store(tmp_path)
    for job_id in ('a', 'b', 'c'):
        store.save(_make_state(job_id='job_' + job_id))

    service = ExecutionService(
        build_default_execution_registry(),
        clock=lambda: '2026-05-04T03:00:00Z',
        state_store=store,
    )
    removed = service.abandon_orphan_persisted_states(active_job_ids=frozenset({'job_a'}))

    assert {'orphan_executions_removed': len(removed)} == {'orphan_executions_removed': 2}
