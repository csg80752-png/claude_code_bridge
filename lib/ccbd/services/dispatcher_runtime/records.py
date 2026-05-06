from __future__ import annotations

from ccbd.api_models import JobEvent, JobRecord, TargetKind
from mailbox_runtime.targets import CMD_ACTOR


def get_job(dispatcher, job_id: str) -> JobRecord | None:
    target = dispatcher._state.target_for_job(job_id)
    if target is not None:
        return dispatcher._job_store.get_latest_target(target[0], target[1], job_id)
    for candidate in dispatcher._config.agents:
        record = dispatcher._job_store.get_latest(candidate, job_id)
        if record is not None:
            dispatcher._state.remember_job(job_id, TargetKind.AGENT, candidate)
            return record
    if bool(getattr(dispatcher._config, 'cmd_enabled', False)):
        record = dispatcher._job_store.get_latest_target(TargetKind.CMD, CMD_ACTOR, job_id)
        if record is not None:
            dispatcher._state.remember_job(job_id, TargetKind.CMD, CMD_ACTOR)
            return record
    return None


def latest_for_agent(dispatcher, agent_name: str) -> JobRecord | None:
    records = dispatcher._job_store.list_agent(agent_name)
    if not records:
        return None
    return records[-1]


def append_job(dispatcher, record: JobRecord) -> None:
    dispatcher._job_store.append(record)
    dispatcher._state.record(record)


def append_event(
    dispatcher,
    record: JobRecord,
    event_type: str,
    payload: dict[str, object],
    *,
    timestamp: str,
) -> None:
    dispatcher._event_store.append(
        JobEvent(
            event_id=dispatcher._new_id('evt'),
            job_id=record.job_id,
            agent_name=record.agent_name,
            target_kind=record.target_kind,
            target_name=record.target_name,
            type=event_type,
            payload=dict(payload),
            timestamp=timestamp,
        )
    )


def rebuild_dispatcher_state(dispatcher) -> None:
    dispatcher._state.rebuild(dispatcher._job_store, agent_names=dispatcher._config.agents)
    if bool(getattr(dispatcher._config, 'cmd_enabled', False)):
        latest_by_job: dict[str, JobRecord] = {}
        order: list[str] = []
        for record in dispatcher._job_store.list_target(TargetKind.CMD, CMD_ACTOR):
            if record.job_id not in latest_by_job:
                order.append(record.job_id)
            latest_by_job[record.job_id] = record
        for job_id in order:
            dispatcher._state.restore_record(latest_by_job[job_id])


__all__ = [
    'append_event',
    'append_job',
    'get_job',
    'latest_for_agent',
    'rebuild_dispatcher_state',
]
