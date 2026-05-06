# Codex Resume Session Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent Codex agents from resuming metadata-only rollout files that create apparently empty sessions after CCB restart.

**Architecture:** Keep the change inside Codex launcher session selection. A stored `.codex-agentN-session` pointer remains the source of truth, but the referenced rollout must contain at least one substantive non-`session_meta` JSONL record before it is used for `codex resume`. Invalid pointers are cleared using the existing stale pointer path.

**Tech Stack:** Python, pytest, existing `provider_backends.codex.launcher_runtime.session_paths` helpers.

---

### Task 1: Reject Metadata-Only Codex Resume Pointers

**Files:**
- Modify: `lib/provider_backends/codex/launcher_runtime/session_paths.py`
- Test: `test/test_codex_launcher_session_paths.py`

- [x] **Step 1: Write the failing test**

Add a test that creates `.ccb/.codex-agent2-session` pointing at a rollout containing only a `session_meta` line. Assert `load_resume_session_id()` returns `None` and clears stale resume fields.

- [x] **Step 2: Run test to verify RED**

Run: `env PYTHONPATH=lib uvx pytest test/test_codex_launcher_session_paths.py::test_load_resume_session_id_rejects_metadata_only_codex_session_path -q`
Expected: FAIL because current code treats any matching session path as resumable.

- [x] **Step 3: Implement minimal helper**

Add a private helper that reads a bounded prefix of the JSONL file and returns False when the only valid record is `type=session_meta` or the file has no substantive record. Use it when validating `codex_session_path` and root glob candidates.

- [x] **Step 4: Run targeted tests**

Run: `env PYTHONPATH=lib uvx pytest test/test_codex_launcher_session_paths.py test/test_codex_session_fields.py -q`
Expected: PASS.

- [x] **Step 5: Run wider launcher/runtime tests**

Run: `env PYTHONPATH=lib uvx pytest test/test_v2_runtime_launch.py::test_codex_launcher_build_start_cmd_uses_agent_scoped_resume_session test/test_v2_runtime_launch.py::test_codex_launcher_build_start_cmd_reads_resume_cmd_from_agent_scoped_session_file -q`
Expected: PASS.

- [x] **Step 6: Prepare review checkpoint**

Do not merge or install before adversarial and second-axis review, per standing policy.

Completed verification:
- RED: `env PYTHONPATH=lib uvx pytest test/test_codex_launcher_session_paths.py::test_load_resume_session_id_rejects_metadata_only_codex_session_path -q` failed before implementation.
- Targeted: `env PYTHONPATH=lib uvx pytest test/test_codex_launcher_session_paths.py test/test_codex_session_fields.py test/test_codex_start_cmd_fields.py test/test_codex_start_cmd_parsing.py -q` passed.
- Wider: `env PYTHONPATH=lib uvx pytest test/test_v2_runtime_launch.py test/test_codex_launcher_session_paths.py test/test_codex_session_fields.py -q` passed.
- Full: `env PYTHONPATH=lib uvx pytest -q` passed with `1865 passed, 17 skipped`.
- Reviews: adversarial and operational subagent reviews passed after one adversarial finding was fixed.
