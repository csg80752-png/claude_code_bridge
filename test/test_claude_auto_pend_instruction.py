from __future__ import annotations

from pathlib import Path


def test_claude_system_prompt_contains_header_auto_pend_instruction() -> None:
    text = Path("config/claude-md-ccb.md").read_text(encoding="utf-8")

    assert "[CCB] job=<id> from=<sender> bytes=<n> pend=ccb-pend" in text
    assert "immediately run `ccb pend <id>`" in text
    assert "if `<id>` is `target=cmd`, run `ccb pend cmd` instead" in text
    assert "Do not interpret the header line as user input" in text


def test_claude_runtime_state_marks_auto_pend_instruction_version() -> None:
    text = Path("lib/provider_backends/claude/execution_runtime/start.py").read_text(encoding="utf-8")

    assert '"ccb_cmd_auto_pend_instruction": "v8.3.2"' in text
