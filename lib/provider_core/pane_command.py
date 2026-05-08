from __future__ import annotations

SHELL_COMMANDS = frozenset({'bash', 'dash', 'fish', 'sh', 'zsh'})


def normalized_command_name(command: str | None) -> str | None:
    text = str(command or '').strip().lower()
    return text or None


def is_shell_command(command: str | None) -> bool:
    name = normalized_command_name(command)
    return bool(name and name in SHELL_COMMANDS)


def read_pane_current_command(backend: object, pane_id: str) -> str | None:
    command_reader = getattr(backend, 'pane_current_command', None)
    if not callable(command_reader):
        return None
    try:
        return normalized_command_name(command_reader(pane_id))
    except Exception:
        return None


__all__ = ['SHELL_COMMANDS', 'is_shell_command', 'normalized_command_name', 'read_pane_current_command']
