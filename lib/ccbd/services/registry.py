from __future__ import annotations

import time
from dataclasses import fields, replace

from agents.models import AgentRuntime, AgentSpec, AgentState, normalize_agent_name
from agents.store import AgentRuntimeStore
from runtime_env import env_default_on
from storage.paths import PathLayout

_ACTIVE_STATES = {AgentState.STARTING, AgentState.IDLE, AgentState.BUSY, AgentState.DEGRADED}

_DIRTY_CHECK_ENV = 'CCB_CCBD_DIRTY_CHECK'
# Any future AgentRuntime field that updates on every heartbeat without semantic
# change must be added here AND covered by a flag-on suppression test.
_RUNTIME_HEARTBEAT_ONLY_FIELDS = frozenset({'last_seen_at'})
_RUNTIME_FORCED_FLUSH_INTERVAL_S = 60.0


class AgentRegistry:
    def __init__(self, layout: PathLayout, config, runtime_store: AgentRuntimeStore | None = None) -> None:
        self._layout = layout
        self._config = config
        self._runtime_store = runtime_store or AgentRuntimeStore(layout)
        self._cache: dict[str, AgentRuntime] = {}
        # Lock-free; assumes the ccbd socket loop is the sole writer to upsert/cache state.
        self._last_disk_flush_at: dict[str, float] = {}
        self._load_existing()

    def _load_existing(self) -> None:
        for agent_name in self._config.agents:
            runtime = self._runtime_store.load(agent_name)
            if runtime is not None:
                self._cache[agent_name] = runtime

    def spec_for(self, agent_name: str) -> AgentSpec:
        normalized = normalize_agent_name(agent_name)
        try:
            return self._config.agents[normalized]
        except KeyError as exc:
            raise KeyError(f'unknown agent: {normalized}') from exc

    def get(self, agent_name: str) -> AgentRuntime | None:
        normalized = normalize_agent_name(agent_name)
        cached = self._cache.get(normalized)
        if cached is not None:
            return cached
        runtime = self._runtime_store.load(normalized)
        if runtime is not None:
            self._cache[normalized] = runtime
        return runtime

    def update_cache_only(self, runtime: AgentRuntime) -> AgentRuntime:
        self.spec_for(runtime.agent_name)
        self._cache[runtime.agent_name] = runtime
        return runtime

    def upsert(self, runtime: AgentRuntime) -> AgentRuntime:
        self.spec_for(runtime.agent_name)

        if not env_default_on(_DIRTY_CHECK_ENV):
            self._runtime_store.save(runtime)
            self._cache[runtime.agent_name] = runtime
            return runtime

        previous = self._cache.get(runtime.agent_name)
        meaningful_change = previous is None or any(
            getattr(previous, f.name) != getattr(runtime, f.name)
            for f in fields(runtime)
            if f.name not in _RUNTIME_HEARTBEAT_ONLY_FIELDS
        )
        last_flush = self._last_disk_flush_at.get(runtime.agent_name)
        flush_due = last_flush is None or (time.monotonic() - last_flush) >= _RUNTIME_FORCED_FLUSH_INTERVAL_S

        if meaningful_change or flush_due:
            self._runtime_store.save(runtime)
            self._last_disk_flush_at[runtime.agent_name] = time.monotonic()
            self._cache[runtime.agent_name] = runtime
            return runtime

        return self.update_cache_only(runtime)

    def remove(self, agent_name: str) -> AgentRuntime | None:
        runtime = self.get(agent_name)
        if runtime is None:
            return None
        stopped = replace(
            runtime,
            state=AgentState.STOPPED,
            pid=None,
            runtime_ref=None,
            session_ref=None,
            socket_path=None,
            queue_depth=0,
            health='stopped',
            runtime_pid=None,
            pane_id=None,
            active_pane_id=None,
            pane_state=None,
            desired_state='stopped',
            reconcile_state='stopped',
            last_failure_reason=None,
        )
        return self.upsert(stopped)

    def list_all(self) -> tuple[AgentRuntime, ...]:
        runtimes: list[AgentRuntime] = []
        for agent_name in sorted(self._config.agents):
            runtime = self.get(agent_name)
            if runtime is not None:
                runtimes.append(runtime)
        return tuple(runtimes)

    def list_alive(self) -> tuple[AgentRuntime, ...]:
        return tuple(runtime for runtime in self.list_all() if runtime.state in _ACTIVE_STATES)

    def list_known_agents(self) -> tuple[str, ...]:
        return tuple(sorted(self._config.agents))


__all__ = ['AgentRegistry']
