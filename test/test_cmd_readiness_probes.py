from __future__ import annotations

from ccbd.services.dispatcher_runtime.reply_delivery_runtime import cmd_readiness_probes


def test_has_prompt_line_accepts_bare_prompt():
    assert cmd_readiness_probes._has_prompt_line("❯ ") is True


def test_has_prompt_line_rejects_prompt_with_tail_content():
    assert cmd_readiness_probes._has_prompt_line("❯ user text") is False


def test_claude_ready_accepts_ready_prompt_with_footer_below():
    text = "\n".join([
        "────────────────────────────────────────────────────────",
        "❯\u00a0",
        "────────────────────────────────────────────────────────",
        "  speed@csk:~/project [Opus] | agent:2.1.119",
        "  ctx:[███░░░░░░░] 36% | mode:default",
    ])

    assert cmd_readiness_probes.claude_ready(text) is True


def test_claude_ready_rejects_footer_without_prompt():
    text = "\n".join([
        "────────────────────────────────────────────────────────",
        "  speed@csk:~/project [Opus] | agent:2.1.119",
        "  ctx:[███░░░░░░░] 36% | mode:default",
    ])

    assert cmd_readiness_probes.claude_ready(text) is False


def test_claude_ready_rejects_typed_prompt():
    assert cmd_readiness_probes.claude_ready("❯ user typing") is False


def test_claude_ready_accepts_common_bare_prompt_variants():
    for prompt in ("$ ", "> ", "→ ", "❯ "):
        assert cmd_readiness_probes.claude_ready(prompt) is True


def test_claude_ready_ignores_transcript_prompt_prefixes_before_current_prompt():
    assert cmd_readiness_probes.claude_ready("$ pytest\n❯ ") is True
    assert cmd_readiness_probes.claude_ready("> quoted output\n❯ ") is True


def test_claude_ready_accepts_ready_marker_after_historical_busy_text():
    text = "assistant reply\nlog says compacting database completed\nType your message"

    assert cmd_readiness_probes.claude_ready(text) is True


def test_claude_ready_accepts_ready_marker_after_historical_modal_text():
    text = "approval was granted earlier\nchoose the old option\nType your message"

    assert cmd_readiness_probes.claude_ready(text) is True


def test_claude_ready_rejects_current_busy_line_even_with_ready_marker():
    assert cmd_readiness_probes.claude_ready("Compacting database\nType your message") is False
    assert cmd_readiness_probes.claude_ready("esc to interrupt\nType your message") is False


def test_claude_ready_rejects_modal_markers_below_prompt():
    assert cmd_readiness_probes.claude_ready("❯\nSelect: /resume") is False


def test_claude_ready_rejects_two_line_approval_modal_above_prompt():
    assert cmd_readiness_probes.claude_ready("Do you want to allow this command?\n❯") is False


def test_claude_ready_rejects_multiline_modal_context_above_prompt_variants():
    blocked = [
        "Trust this folder before continuing?\n❯",
        "Choose a session to resume\n> ",
        "Approval required for command\n$ ",
        "Press enter to confirm\n→ ",
    ]
    for text in blocked:
        assert cmd_readiness_probes.claude_ready(text) is False


def test_claude_ready_rejects_busy_marker_below_prompt():
    assert cmd_readiness_probes.claude_ready("❯\nesc to interrupt") is False


def test_claude_ready_trims_trailing_blank_rows_before_bottom_window():
    text = "\n".join([
        "recap text",
        "────────────────────────────────────────────────────────",
        "❯\u00a0",
        "────────────────────────────────────────────────────────",
        "  speed@csk:~/project [Opus] | agent:2.1.119",
        "",
        "",
        "",
        "",
        "",
        "",
    ])

    assert cmd_readiness_probes.claude_ready(text) is True


def test_claude_ready_ignores_ready_marker_outside_bottom_window():
    text = "❯ old prompt\n" + "\n".join(f"line {i}" for i in range(20))

    assert cmd_readiness_probes.claude_ready(text) is False


def test_readiness_probe_registry_is_claude_only():
    assert cmd_readiness_probes.READINESS_PROBES == {
        "claude": cmd_readiness_probes.claude_ready,
    }
