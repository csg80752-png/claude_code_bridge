from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess


_ISOLATED_HOME_DIR = "codex-home"
_POLICY_FILENAME = ".isolation-policy-version"
_POLICY_VERSION = "r1"
_INHERIT_ALLOWLIST = ("config.toml", "auth.json", "skills", "commands")
_CP_BIN = shutil.which("cp") or "/bin/cp"


@dataclass(frozen=True)
class CodexSessionsRoot:
    path: Path
    is_isolated: bool


def prepare_codex_home_overrides(runtime_dir: Path, profile) -> dict[str, str]:
    resolved_runtime = Path(runtime_dir)
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
    config_path = isolated_home / "config.toml"
    if not config_path.exists():
        config_path.write_text("# ccb isolated codex config\n", encoding="utf-8")
    _write_policy_sentinel(isolated_home)
    return isolated_home


def resolve_codex_sessions_root(runtime_dir: Path, *, profile=None) -> CodexSessionsRoot:
    resolved_runtime = Path(runtime_dir)
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
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")).expanduser()


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
    "CodexSessionsRoot",
    "codex_runtime_dir_from_session_file",
    "isolated_home_for_runtime",
    "isolation_opt_out",
    "prepare_codex_home_overrides",
    "prepare_codex_isolated_home",
    "resolve_codex_sessions_root",
]
