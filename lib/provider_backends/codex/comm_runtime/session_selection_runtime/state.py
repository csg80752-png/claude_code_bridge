from __future__ import annotations


def follow_workspace_sessions(reader) -> bool:
    return bool(getattr(reader, "_follow_workspace_sessions", False) and getattr(reader, "_work_dir", None))


def isolated_to_root(reader) -> bool:
    return bool(getattr(reader, "_isolated_to_root", False))


__all__ = ['follow_workspace_sessions', 'isolated_to_root']
