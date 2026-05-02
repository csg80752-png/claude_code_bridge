"""Readiness probes for cmd pane reply delivery."""

from __future__ import annotations

from enum import Enum
from typing import Callable, Mapping


class ReadinessOutcome(str, Enum):
    READY = 'ready'
    NOT_READY = 'not_ready'
    PROBE_UNAVAILABLE = 'probe_unavailable'
    UNKNOWN_CONSUMER = 'unknown_consumer'


_BOTTOM_LINES = 10
_MODAL_CONTEXT_LINES = 4
_PROMPT_PREFIXES = ('❯', '$', '→', '>')


def _has_prompt_line(text: str) -> bool:
    for line in str(text or '').splitlines():
        tail = _prompt_tail(line)
        if tail is None:
            continue
        if not tail or tail.isspace():
            return True
    return False


def _has_prompt_line_with_tail(text: str) -> bool:
    for line in str(text or '').splitlines():
        tail = _prompt_tail(line)
        if tail is None:
            continue
        if tail and not tail.isspace():
            return True
    return False


def _last_visual_line(lines: list[str]) -> str:
    for line in reversed(lines):
        if line.strip():
            return line
    return ''


def _find_last_bare_prompt_line_idx(lines: list[str]) -> int | None:
    for idx in range(len(lines) - 1, -1, -1):
        tail = _prompt_tail(lines[idx])
        if tail is None:
            continue
        if not tail.strip():
            return idx
    return None


def _prompt_tail(line: str) -> str | None:
    stripped = str(line or '').lstrip()
    for prefix in _PROMPT_PREFIXES:
        if stripped.startswith(prefix):
            return stripped[len(prefix):]
    return None


def _has_typed_prompt_tail(text: str) -> bool:
    lines = str(text or '').splitlines()
    tail = _prompt_tail(_last_visual_line(lines))
    return bool(tail and not tail.isspace())


def _claude_ready_core_strict(text: str) -> bool:
    lowered = str(text or '').lower()
    return 'type your message' in lowered or 'for shortcuts' in lowered


def _has_modal_or_picker_markers(text: str) -> bool:
    normalized = _last_visual_line(str(text or '').splitlines())
    return _line_has_modal_or_picker_marker(normalized)


def _has_modal_or_picker_markers_near_prompt(lines: list[str], prompt_idx: int) -> bool:
    start = max(0, prompt_idx - (_MODAL_CONTEXT_LINES - 1))
    for line in lines[start:]:
        if _line_has_modal_or_picker_marker(line):
            return True
    return False


def _line_has_modal_or_picker_marker(line: str) -> bool:
    negative_markers = (
        'do you want',
        'trust this folder',
        'loading configuration',
        'select a session',
        'select:',
        'choose',
        'press enter',
        'approval',
    )
    slash_commands = ('/resume', '/clear', '/config', '/memory')
    stripped = str(line or '').strip()
    lowered = stripped.lower()
    if any(marker in lowered for marker in negative_markers):
        return True
    if any(stripped.startswith(command) for command in slash_commands):
        return True
    return False


def _has_busy_words(text: str) -> bool:
    busy_prefixes = (
        'loading',
        'compacting',
        'processing',
        'fetching',
        'connecting',
        'thinking',
    )
    for line in str(text or '').splitlines():
        lowered = line.strip().lower()
        if any(lowered.startswith(word) for word in busy_prefixes):
            return True
    return False


def _has_busy_state_markers(text: str) -> bool:
    lowered = str(text or '').lower()
    return 'esc to interrupt' in lowered or _has_busy_words(text)


def _has_any_not_ready_marker(text: str) -> bool:
    return _has_modal_or_picker_markers(text) or _has_busy_state_markers(text)


def _bottom_screen_text(text: str) -> str:
    lines = str(text or '').splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    return '\n'.join(lines[-_BOTTOM_LINES:])


def claude_ready(text: str) -> bool:
    """Check only the bottom pane lines to avoid stale scrollback matches."""
    bottom = _bottom_screen_text(text)
    lines = bottom.splitlines()
    prompt_idx = _find_last_bare_prompt_line_idx(lines)

    if prompt_idx is not None:
        below_prompt = '\n'.join(lines[prompt_idx + 1:])
        if _has_typed_prompt_tail(bottom):
            return False
        if _has_modal_or_picker_markers_near_prompt(lines, prompt_idx):
            return False
        if _has_any_not_ready_marker(below_prompt):
            return False
        return True

    if _has_typed_prompt_tail(bottom):
        return False
    if _has_modal_or_picker_markers(bottom):
        return False
    if _has_busy_state_markers(bottom):
        return False
    if _claude_ready_core_strict(bottom):
        return True
    return False


READINESS_PROBES: Mapping[str, Callable[[str], bool]] = {
    'claude': claude_ready,
}


__all__ = [
    'ReadinessOutcome',
    'READINESS_PROBES',
    'claude_ready',
]
