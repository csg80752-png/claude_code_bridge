from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_install_remove_codex_mcp_is_called_only_inside_install_all_locked() -> None:
    text = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
    before_locked, locked_and_after = text.split("install_all_locked() {", 1)
    locked_body, after_locked = locked_and_after.split("install_all() {", 1)

    assert "remove_codex_mcp" in locked_body
    assert "remove_codex_mcp" not in after_locked.split("uninstall_claude_md_config()", 1)[0]
    assert before_locked.count("remove_codex_mcp()") == 1


def test_install_prefix_mutation_helpers_assert_install_lock_and_guard() -> None:
    text = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
    for function in ("replace_install_prefix_from_staging", "remove_install_prefix_guarded"):
        body = text.split(f"{function}() {{", 1)[1].split("\n}", 1)[0]
        assert "assert_install_mutation_guard" in body
        assert "guard_install_prefix" in body


def test_storage_writes_route_through_state_mutation_guard() -> None:
    json_store = (REPO_ROOT / "lib" / "storage" / "json_store.py").read_text(encoding="utf-8")
    jsonl_store = (REPO_ROOT / "lib" / "storage" / "jsonl_store.py").read_text(encoding="utf-8")

    assert "guarded_state_mutation_for_path" in json_store
    assert "guarded_state_mutation_for_path" in jsonl_store


def test_guarded_mutation_allowlist_names_mount_registry_and_jsonl() -> None:
    text = (REPO_ROOT / "lib" / "ccbd" / "state_mutation_guard.py").read_text(encoding="utf-8")

    assert '"mount_lease"' in text
    assert '"registry"' in text
    assert '"jsonl_global_state"' in text
    assert "ALLOWED_INSTALL_MUTATION_SYMBOLS" in text
