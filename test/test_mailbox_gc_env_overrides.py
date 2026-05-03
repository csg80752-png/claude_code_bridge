from __future__ import annotations

from mailbox_kernel import gc as mailbox_gc


def test_mailbox_gc_env_overrides_parse_and_cap_bytes(monkeypatch) -> None:
    monkeypatch.setenv("CCB_MAILBOX_MAX_AGE_DAYS", "3")
    monkeypatch.setenv("CCB_MAILBOX_MAX_BYTES", "999999999")

    options = mailbox_gc.resolve_mailbox_gc_options()

    assert options.max_pending_age_days == 3
    assert options.max_bytes_per_mailbox == mailbox_gc.MAX_MAILBOX_BYTES


def test_mailbox_gc_env_overrides_invalid_values_fall_back(monkeypatch) -> None:
    monkeypatch.setenv("CCB_MAILBOX_MAX_AGE_DAYS", "bad")
    monkeypatch.setenv("CCB_MAILBOX_MAX_BYTES", "bad")

    options = mailbox_gc.resolve_mailbox_gc_options(default_max_age_days=5, default_max_bytes=1234)

    assert options.max_pending_age_days == 5
    assert options.max_bytes_per_mailbox == 1234
