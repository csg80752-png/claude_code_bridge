from __future__ import annotations

from pathlib import Path

from cli.services.doctor_runtime.system import requirements_summary, stale_provider_directories
from provider_core.registry import CORE_PROVIDER_NAMES, OPTIONAL_PROVIDER_NAMES


def _provider_stub(root: Path, name: str) -> None:
    provider_dir = root / name
    provider_dir.mkdir()
    for filename in ('__init__.py', 'comm.py', 'protocol.py', 'session.py'):
        (provider_dir / filename).write_text('', encoding='utf-8')


def test_requirements_summary_emits_provider_visibility_keys() -> None:
    payload = requirements_summary()

    assert payload['supported_providers'] == tuple(CORE_PROVIDER_NAMES + OPTIONAL_PROVIDER_NAMES)
    assert payload['stale_provider_directories'] == stale_provider_directories()


def test_stale_provider_directories_are_derived_from_unregistered_backend_stubs(tmp_path: Path) -> None:
    backends_dir = tmp_path / 'provider_backends'
    backends_dir.mkdir()
    _provider_stub(backends_dir, 'codex')
    _provider_stub(backends_dir, 'qwen')
    _provider_stub(backends_dir, 'future')
    (backends_dir / 'pane_log_support').mkdir()
    (backends_dir / 'pane_log_support' / '__init__.py').write_text('', encoding='utf-8')

    stale = stale_provider_directories(
        backends_dir,
        registered_providers=('codex', 'future'),
    )

    assert stale == ('qwen',)
