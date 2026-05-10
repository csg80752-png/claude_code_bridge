from __future__ import annotations

from pathlib import Path
import platform
import shutil
import sys

from cli.management import find_install_dir, get_version_info
from provider_core.registry import CORE_PROVIDER_NAMES, OPTIONAL_PROVIDER_NAMES
from provider_core.runtime_shared import provider_executable

PROVIDER_BACKEND_STUB_FILES = ('comm.py', 'protocol.py', 'session.py')


def installation_summary() -> dict[str, object]:
    install_dir = find_install_dir(_script_root())
    info = get_version_info(install_dir)
    return {
        'path': str(install_dir),
        'version': info.get('version'),
        'commit': info.get('commit'),
        'date': info.get('date'),
        'channel': info.get('channel'),
        'platform': info.get('platform'),
        'arch': info.get('arch'),
        'build_time': info.get('build_time'),
        'installed_at': info.get('installed_at'),
        'source_kind': info.get('source_kind'),
        'install_mode': info.get('install_mode'),
    }


def requirements_summary() -> dict[str, object]:
    tmux_path = shutil.which('tmux')
    wired_providers = tuple(CORE_PROVIDER_NAMES + OPTIONAL_PROVIDER_NAMES)
    providers = []
    for provider in wired_providers:
        executable = provider_executable(provider)
        command_path = shutil.which(executable)
        providers.append(
            {
                'provider': provider,
                'executable': executable,
                'available': command_path is not None,
                'path': command_path,
            }
        )
    return {
        'python_executable': sys.executable,
        'python_version': platform.python_version(),
        'tmux_available': tmux_path is not None,
        'tmux_path': tmux_path,
        'supported_providers': wired_providers,
        'stale_provider_directories': stale_provider_directories(),
        'provider_commands': providers,
    }


def stale_provider_directories(
    provider_backends_dir: Path | None = None,
    *,
    registered_providers: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    backends_dir = provider_backends_dir or _script_root() / 'lib' / 'provider_backends'
    registered = set(registered_providers or tuple(CORE_PROVIDER_NAMES + OPTIONAL_PROVIDER_NAMES))
    if not backends_dir.is_dir():
        return ()
    stale = []
    for child in sorted(backends_dir.iterdir(), key=lambda path: path.name):
        if not child.is_dir() or child.name.startswith('__') or child.name in registered:
            continue
        if _looks_like_provider_backend(child):
            stale.append(child.name)
    return tuple(stale)


def _looks_like_provider_backend(path: Path) -> bool:
    return (path / '__init__.py').is_file() and all((path / name).is_file() for name in PROVIDER_BACKEND_STUB_FILES)


def _script_root() -> Path:
    return Path(__file__).resolve().parents[4]


__all__ = [
    'installation_summary',
    'requirements_summary',
    'stale_provider_directories',
]
