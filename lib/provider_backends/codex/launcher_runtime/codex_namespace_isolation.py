from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess


_ISOLATED_HOME_DIR = "codex-home"
_POLICY_FILENAME = ".isolation-policy-version"
_POLICY_VERSION = "r1"
_INHERIT_ALLOWLIST = ("config.toml", "skills", "commands")
_SYNC_ALLOWLIST = ("config.toml", "skills", "commands", "rules")
_CP_BIN = shutil.which("cp") or "/bin/cp"


@dataclass(frozen=True)
class CodexSessionsRoot:
    path: Path
    is_isolated: bool


@dataclass(frozen=True)
class CodexHomeSyncResult:
    path: Path
    synced: tuple[str, ...]
    skipped_auth: bool


def prepare_codex_home_overrides(runtime_dir: Path, profile) -> dict[str, str]:
    resolved_runtime = Path(runtime_dir)
    explicit_profile_env = explicit_codex_home_overrides(getattr(profile, "env", {}) if profile is not None else {})
    if explicit_profile_env:
        return explicit_profile_env
    if profile is not None and getattr(profile, "runtime_home", None):
        runtime_home = Path(str(profile.runtime_home)).expanduser()
        runtime_home.mkdir(parents=True, exist_ok=True)
        (runtime_home / "sessions").mkdir(parents=True, exist_ok=True)
        return {
            "CODEX_HOME": str(runtime_home),
            "CODEX_SESSION_ROOT": str(runtime_home / "sessions"),
        }
    if isolation_opt_out():
        return {}
    isolated_home = prepare_codex_isolated_home(resolved_runtime)
    return {
        "CODEX_HOME": str(isolated_home),
        "CODEX_SESSION_ROOT": str(isolated_home / "sessions"),
    }


def explicit_codex_home_overrides(env: dict[str, object] | None) -> dict[str, str]:
    source = dict(env or {})
    raw_home = str(source.get("CODEX_HOME") or source.get("codex_home") or "").strip()
    raw_session_root = str(source.get("CODEX_SESSION_ROOT") or source.get("codex_session_root") or "").strip()
    if not raw_home and not raw_session_root:
        return {}
    overrides: dict[str, str] = {}
    home: Path | None = None
    if raw_home:
        home = Path(raw_home).expanduser()
        overrides["CODEX_HOME"] = str(home)
    if raw_session_root:
        overrides["CODEX_SESSION_ROOT"] = str(Path(raw_session_root).expanduser())
    elif home is not None:
        overrides["CODEX_SESSION_ROOT"] = str(home / "sessions")
    return overrides


def codex_home_session_payload(
    runtime_dir: Path,
    *,
    profile=None,
    explicit_env: dict[str, object] | None = None,
) -> dict[str, str]:
    env = explicit_codex_home_overrides(explicit_env) or prepare_codex_home_overrides(runtime_dir, profile)
    payload: dict[str, str] = {}
    if env.get("CODEX_HOME"):
        payload["codex_home"] = env["CODEX_HOME"]
    if env.get("CODEX_SESSION_ROOT"):
        payload["codex_session_root"] = env["CODEX_SESSION_ROOT"]
    return payload


def prepare_codex_isolated_home(runtime_dir: Path, *, source_home: Path | None = None) -> Path:
    resolved_runtime = Path(runtime_dir)
    source = Path(source_home) if source_home is not None else system_codex_home()
    isolated_home = isolated_home_for_runtime(resolved_runtime)
    isolated_home.mkdir(parents=True, exist_ok=True)
    (isolated_home / "sessions").mkdir(parents=True, exist_ok=True)
    for name in _INHERIT_ALLOWLIST:
        src = source / name
        dst = isolated_home / name
        if not src.exists():
            continue
        _refresh_inherited_entry(src, dst)
    auth_source = source / "auth.json"
    if auth_source.is_file() and not auth_source.is_symlink():
        _refresh_secret_file(auth_source, isolated_home / "auth.json")
    config_path = isolated_home / "config.toml"
    if not config_path.exists():
        config_path.write_text("# ccb isolated codex config\n", encoding="utf-8")
    _write_policy_sentinel(isolated_home)
    return isolated_home


def sync_codex_home_from_source(
    isolated_home: Path,
    *,
    source_home: Path | None = None,
    include_auth: bool = False,
) -> CodexHomeSyncResult:
    source = Path(source_home) if source_home is not None else system_codex_home()
    target_home = Path(isolated_home)
    if target_home.is_symlink():
        raise ValueError("codex home must not be a symlink")
    sentinel = target_home / _POLICY_FILENAME
    if sentinel.is_symlink():
        raise ValueError("policy sentinel must not be a symlink")
    target_home.mkdir(parents=True, exist_ok=True)
    (target_home / "sessions").mkdir(parents=True, exist_ok=True)

    synced: list[str] = []
    for name in _SYNC_ALLOWLIST:
        src = source / name
        if not src.exists() or src.is_symlink():
            continue
        _refresh_physical_entry(src, target_home / name)
        synced.append(name)

    auth_source = source / "auth.json"
    auth_source_is_file = auth_source.is_file() and not auth_source.is_symlink()
    skipped_auth = auth_source.exists() and (not include_auth or not auth_source_is_file)
    if auth_source_is_file and include_auth:
        _refresh_secret_file(auth_source, target_home / "auth.json")
        synced.append("auth.json")

    _write_policy_sentinel(target_home)
    return CodexHomeSyncResult(
        path=target_home,
        synced=tuple(synced),
        skipped_auth=skipped_auth,
    )


def resolve_codex_sessions_root(
    runtime_dir: Path,
    *,
    profile=None,
    explicit_env: dict[str, object] | None = None,
) -> CodexSessionsRoot:
    resolved_runtime = Path(runtime_dir)
    explicit_root_env = explicit_codex_home_overrides(explicit_env)
    if explicit_root_env.get("CODEX_SESSION_ROOT"):
        root = Path(explicit_root_env["CODEX_SESSION_ROOT"]).expanduser()
        return CodexSessionsRoot(
            path=root,
            is_isolated=_path_is_under_ccbd_namespace(root.parent, resolved_runtime),
        )
    explicit_profile_env = explicit_codex_home_overrides(getattr(profile, "env", {}) if profile is not None else {})
    if explicit_profile_env.get("CODEX_SESSION_ROOT"):
        root = Path(explicit_profile_env["CODEX_SESSION_ROOT"]).expanduser()
        return CodexSessionsRoot(
            path=root,
            is_isolated=_path_is_under_ccbd_namespace(root.parent, resolved_runtime),
        )
    if profile is not None and getattr(profile, "runtime_home", None):
        runtime_home = Path(str(profile.runtime_home)).expanduser()
        return CodexSessionsRoot(
            path=runtime_home / "sessions",
            is_isolated=_path_is_under_ccbd_namespace(runtime_home, resolved_runtime),
        )
    if isolation_opt_out():
        return CodexSessionsRoot(path=legacy_sessions_root(), is_isolated=False)
    isolated_home = isolated_home_for_runtime(resolved_runtime)
    return CodexSessionsRoot(path=isolated_home / "sessions", is_isolated=True)


def codex_runtime_dir_from_session_file(session_file: object | None) -> Path | None:
    if session_file is None:
        return None
    try:
        path = Path(str(session_file)).expanduser()
    except Exception:
        return None
    ccb_dir = path.parent
    if ccb_dir.name != ".ccb":
        return None
    agent_name = _agent_name_from_session_filename(path.name)
    if not agent_name:
        return None
    return ccb_dir / "agents" / agent_name / "provider-runtime" / "codex"


def isolated_home_for_runtime(runtime_dir: Path) -> Path:
    return Path(runtime_dir) / _ISOLATED_HOME_DIR


def isolation_opt_out() -> bool:
    return str(os.environ.get("CCB_CODEX_NAMESPACE_ISOLATION", "") or "").strip() == "0"


def legacy_sessions_root() -> Path:
    explicit_root = str(os.environ.get("CODEX_SESSION_ROOT") or "").strip()
    if explicit_root:
        return Path(explicit_root).expanduser().resolve()
    return (Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser() / "sessions").resolve()


def system_codex_home() -> Path:
    explicit_home = str(os.environ.get("CODEX_HOME") or "").strip()
    if explicit_home:
        codex_home = Path(explicit_home).expanduser()
        if not _path_is_ccb_isolated_codex_home(codex_home):
            return codex_home
    return Path.home() / ".codex"


def _path_is_ccb_isolated_codex_home(path: Path) -> bool:
    parts = path.parts
    if len(parts) < 6:
        return False
    return (
        parts[-1] == _ISOLATED_HOME_DIR
        and parts[-2] == "codex"
        and parts[-3] == "provider-runtime"
        and parts[-5] == "agents"
        and parts[-6] == ".ccb"
    )


def _agent_name_from_session_filename(name: str) -> str | None:
    if name == ".codex-session":
        return "codex"
    prefix = ".codex-"
    suffix = "-session"
    if name.startswith(prefix) and name.endswith(suffix):
        agent_name = name[len(prefix) : -len(suffix)].strip()
        return agent_name or None
    return None


def _refresh_inherited_entry(source: Path, target: Path) -> None:
    if source.is_file() and target.exists():
        try:
            if source.stat().st_ino == target.stat().st_ino and source.stat().st_dev == target.stat().st_dev:
                return
        except OSError:
            pass
    _remove_path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        _run_cp(["-al", str(source), str(target)])
        return
    except Exception:
        _remove_path(target)
    try:
        _run_cp(["-a", str(source), str(target)])
    except Exception:
        _remove_path(target)
        if source.is_file():
            shutil.copy2(source, target)
        elif source.is_dir():
            shutil.copytree(source, target, symlinks=True)


def _refresh_secret_file(source: Path, target: Path) -> None:
    _remove_path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _refresh_physical_entry(source: Path, target: Path) -> None:
    _remove_path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_file():
        shutil.copy2(source, target)
    elif source.is_dir():
        shutil.copytree(source, target, symlinks=True)


def _run_cp(args: list[str]) -> None:
    subprocess.run([_CP_BIN, *args], check=True, capture_output=True, timeout=30)


def _remove_path(path: Path) -> None:
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    except FileNotFoundError:
        return


def _write_policy_sentinel(isolated_home: Path) -> None:
    (isolated_home / _POLICY_FILENAME).write_text(_POLICY_VERSION + "\n", encoding="utf-8")


def _path_is_under_ccbd_namespace(target: Path, runtime_dir: Path) -> bool:
    try:
        return Path(target).expanduser().resolve() == isolated_home_for_runtime(Path(runtime_dir)).resolve()
    except Exception:
        return False


__all__ = [
    "CodexHomeSyncResult",
    "CodexSessionsRoot",
    "codex_home_session_payload",
    "codex_runtime_dir_from_session_file",
    "explicit_codex_home_overrides",
    "isolated_home_for_runtime",
    "isolation_opt_out",
    "prepare_codex_home_overrides",
    "prepare_codex_isolated_home",
    "resolve_codex_sessions_root",
    "sync_codex_home_from_source",
    "system_codex_home",
]
