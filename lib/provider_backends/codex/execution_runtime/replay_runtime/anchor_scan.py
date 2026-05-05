from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from provider_backends.codex.comm_runtime.log_entries import extract_entry
from provider_core.protocol import REQ_ID_PREFIX


# Reasons returned when Shape A replay precondition fails. Operator
# recovery + tests assert on these literals; treat them as a stable enum.
REPLAY_OK = "ok"
REPLAY_FAIL_NO_LOG = "no_log_path"
REPLAY_FAIL_IO = "io_error"
REPLAY_FAIL_ANCHOR_NOT_FOUND = "anchor_not_found"
REPLAY_FAIL_FOREIGN_ANCHOR = "foreign_anchor"
REPLAY_FAIL_ORPHAN_TURN = "orphan_turn_no_assistant"
REPLAY_FAIL_NO_TERMINAL = "no_terminal_for_turn"
REPLAY_FAIL_LATEST_UNRELATED = "latest_assistant_unrelated_to_anchor"


@dataclass
class ReplayResult:
    status: str = REPLAY_FAIL_ANCHOR_NOT_FOUND
    turn_id: str = ""
    anchor_offset: int = -1
    assistant_entries: list[dict[str, Any]] = field(default_factory=list)
    terminal_entry: dict[str, Any] | None = None
    raw_lines: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.status == REPLAY_OK


def replay_anchor_bound(jsonl_path: Path | None, *, request_anchor: str) -> ReplayResult:
    """Locate the anchor for ``request_anchor`` and collect the matching turn's
    assistant + terminal entries. Returns success only when:
      - the anchor user_message is present,
      - the very next entry that carries a turn_id resolves the bound turn,
      - no foreign ``CCB_REQ_ID`` line appears between the anchor and the
        terminal entry,
      - and at least one assistant entry plus a terminal entry are found
        for that turn_id.

    Misbinding rejections (per plan §A vs C decision rule):
      - foreign anchor (different job's CCB_REQ_ID after ours but before our
        terminal),
      - orphan turn (anchor present but no matching assistant),
      - latest unrelated turn (assistant entry exists past anchor but
        belongs to a different turn_id).
    """

    result = ReplayResult()

    if jsonl_path is None:
        result.status = REPLAY_FAIL_NO_LOG
        return result

    request_anchor = (request_anchor or "").strip()
    if not request_anchor:
        result.status = REPLAY_FAIL_ANCHOR_NOT_FOUND
        return result

    expected_marker = f"{REQ_ID_PREFIX} {request_anchor}"
    foreign_prefix = f"{REQ_ID_PREFIX} "

    try:
        with jsonl_path.open("rb") as handle:
            offset = 0
            anchor_offset = -1
            anchor_turn_id = ""
            bound_turn_id = ""
            assistant_entries: list[dict[str, Any]] = []
            terminal_entry: dict[str, Any] | None = None
            seen_assistant_for_other_turn = False

            for raw in handle:
                line_offset = offset
                offset += len(raw)
                stripped = raw.strip()
                if not stripped:
                    continue

                try:
                    entry = json.loads(stripped)
                except (ValueError, json.JSONDecodeError):
                    continue
                if not isinstance(entry, dict):
                    continue

                normalized = extract_entry(entry)
                role = (normalized.get("role") if normalized else "") or ""
                role = str(role).strip().lower()
                text = str((normalized or {}).get("text") or "")
                entry_turn_id = _entry_turn_id(entry, normalized)

                if anchor_offset < 0:
                    if role == "user" and expected_marker in text:
                        anchor_offset = line_offset
                        anchor_turn_id = entry_turn_id
                        if anchor_turn_id:
                            bound_turn_id = anchor_turn_id
                        continue
                    if role == "user" and _has_foreign_anchor(text, expected_marker, foreign_prefix):
                        # Pre-anchor foreign req lives in the same jsonl.
                        # Tracked but not fatal — only fatal AFTER our anchor.
                        continue
                    continue

                if role == "user":
                    if _has_foreign_anchor(text, expected_marker, foreign_prefix):
                        result.status = REPLAY_FAIL_FOREIGN_ANCHOR
                        result.anchor_offset = anchor_offset
                        result.turn_id = bound_turn_id
                        return result
                    continue

                if not bound_turn_id and entry_turn_id:
                    bound_turn_id = entry_turn_id

                if role == "assistant":
                    if not entry_turn_id:
                        # Cannot prove the assistant entry belongs to our
                        # turn. Per the plan: "requires_turn_id=True" is
                        # the contamination predicate; treat as unrelated.
                        seen_assistant_for_other_turn = True
                        continue
                    if bound_turn_id and entry_turn_id != bound_turn_id:
                        seen_assistant_for_other_turn = True
                        continue
                    assistant_entries.append(normalized or {"role": "assistant", "text": text, "turn_id": entry_turn_id})
                    continue

                if role == "system":
                    payload_type = str((normalized or {}).get("payload_type") or "").strip().lower()
                    if payload_type in {"task_complete", "turn_aborted"}:
                        if not entry_turn_id:
                            continue
                        if bound_turn_id and entry_turn_id != bound_turn_id:
                            continue
                        terminal_entry = normalized
                        break

            if anchor_offset < 0:
                result.status = REPLAY_FAIL_ANCHOR_NOT_FOUND
                return result

            result.anchor_offset = anchor_offset
            result.turn_id = bound_turn_id
            result.assistant_entries = assistant_entries
            result.terminal_entry = terminal_entry

            if not assistant_entries:
                if seen_assistant_for_other_turn:
                    result.status = REPLAY_FAIL_LATEST_UNRELATED
                else:
                    result.status = REPLAY_FAIL_ORPHAN_TURN
                return result

            if terminal_entry is None:
                result.status = REPLAY_FAIL_NO_TERMINAL
                return result

            result.status = REPLAY_OK
            return result
    except OSError:
        result.status = REPLAY_FAIL_IO
        return result


def _entry_turn_id(entry: dict[str, Any], normalized: dict[str, Any] | None) -> str:
    if normalized:
        value = normalized.get("turn_id")
        if value:
            return str(value).strip()
    raw_turn = entry.get("turn_id")
    if raw_turn:
        return str(raw_turn).strip()
    payload = entry.get("payload")
    if isinstance(payload, dict):
        payload_turn = payload.get("turn_id")
        if payload_turn:
            return str(payload_turn).strip()
    return ""


def _has_foreign_anchor(text: str, expected_marker: str, foreign_prefix: str) -> bool:
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith(foreign_prefix):
            continue
        if stripped.startswith(expected_marker):
            continue
        return True
    return False


__all__ = [
    "REPLAY_FAIL_ANCHOR_NOT_FOUND",
    "REPLAY_FAIL_FOREIGN_ANCHOR",
    "REPLAY_FAIL_IO",
    "REPLAY_FAIL_LATEST_UNRELATED",
    "REPLAY_FAIL_NO_LOG",
    "REPLAY_FAIL_NO_TERMINAL",
    "REPLAY_FAIL_ORPHAN_TURN",
    "REPLAY_OK",
    "ReplayResult",
    "replay_anchor_bound",
]
