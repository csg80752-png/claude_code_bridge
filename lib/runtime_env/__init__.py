from __future__ import annotations

import os


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    value = raw.strip().lower()
    if value in ("0", "false", "no", "off"):
        return False
    if value in ("1", "true", "yes", "on"):
        return True
    return default


def env_default_on(name: str) -> bool:
    """Production-default kill switch — True unless name is set to 0/false/no/off.

    Use for flags shipped default-ON in v8.1+ (CCB_CCBD_DIRTY_CHECK,
    CCB_CCBD_READAMP_CACHE, etc.) where unsetting the env should keep the
    feature enabled and explicit rollback requires a falsy token.
    """
    return env_bool(name, default=True)


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw.strip())
    except (ValueError, TypeError):
        return default


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw.strip())
    except (ValueError, TypeError):
        return default


__all__ = ['env_bool', 'env_default_on', 'env_float', 'env_int']
