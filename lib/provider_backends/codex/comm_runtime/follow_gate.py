from __future__ import annotations

import os


def workspace_follow_enabled() -> bool:
    raw = str(os.environ.get("CCB_CODEX_FOLLOW_WORKSPACE", "") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


__all__ = ["workspace_follow_enabled"]
