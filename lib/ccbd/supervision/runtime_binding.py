from __future__ import annotations

from agents.models import RuntimeMode

BINDING_FIELDS = ('runtime_ref', 'session_ref', 'pane_id', 'active_pane_id')


def runtime_binding_missing(runtime) -> bool:
    for field_name in BINDING_FIELDS:
        if str(getattr(runtime, field_name, None) or '').strip():
            return False
    return True


def runtime_mode_requires_binding(runtime) -> bool:
    backend_type = str(getattr(runtime, 'backend_type', None) or '').strip()
    return backend_type not in {RuntimeMode.HEADLESS.value, RuntimeMode.PTY_BACKED.value}


def runtime_requires_binding_and_is_unbound(runtime) -> bool:
    return runtime_mode_requires_binding(runtime) and runtime_binding_missing(runtime)


__all__ = [
    'BINDING_FIELDS',
    'runtime_binding_missing',
    'runtime_mode_requires_binding',
    'runtime_requires_binding_and_is_unbound',
]
