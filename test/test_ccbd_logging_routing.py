from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

from ccbd.logging_runtime import configure_ccbd_logging
from cli.services.diagnostics_runtime.sources import project_root_sources
from storage.paths import PathLayout


def test_configure_ccbd_logging_routes_warning_to_runtime_log_once(tmp_path: Path) -> None:
    log_path = tmp_path / "ccbd" / "runtime.log"
    logger = logging.getLogger("ccbd.services.dispatcher_runtime.reply_delivery_runtime.preparation_service")

    configure_ccbd_logging(log_path)
    configure_ccbd_logging(log_path)

    logger.warning("routing-probe")

    lines = log_path.read_text(encoding="utf-8").splitlines()
    matching = [line for line in lines if "routing-probe" in line]
    assert len(matching) == 1
    assert "WARNING" in matching[0]
    assert "ccbd.services.dispatcher_runtime.reply_delivery_runtime.preparation_service" in matching[0]


def test_ccbd_main_configures_runtime_logging_before_serving(tmp_path: Path, monkeypatch) -> None:
    import ccbd.main as main_module

    class FakeApp:
        def __init__(self, project_root: str) -> None:
            self.paths = SimpleNamespace(ccbd_dir=Path(project_root) / ".ccb" / "ccbd")

        def serve_forever(self) -> None:
            logging.getLogger("ccbd.services.example").warning("startup-routing-probe")
            raise KeyboardInterrupt

        def shutdown(self) -> None:
            pass

    monkeypatch.setattr(main_module, "CcbdApp", FakeApp)

    assert main_module.main(["--project", str(tmp_path)]) == 130

    runtime_log = tmp_path / ".ccb" / "ccbd" / "ccbd.runtime.log"
    text = runtime_log.read_text(encoding="utf-8")
    assert "startup-routing-probe" in text


def test_keeper_main_configures_runtime_logging_before_running(tmp_path: Path, monkeypatch) -> None:
    import ccbd.keeper_main as keeper_main_module

    class FakeKeeper:
        def __init__(self, project_root: str) -> None:
            self.paths = SimpleNamespace(ccbd_dir=Path(project_root) / ".ccb" / "ccbd")

        def run_forever(self) -> int:
            logging.getLogger("ccbd.keeper.example").warning("keeper-routing-probe")
            raise KeyboardInterrupt

    monkeypatch.setattr(keeper_main_module, "ProjectKeeper", FakeKeeper)

    assert keeper_main_module.main(["--project", str(tmp_path)]) == 130

    runtime_log = tmp_path / ".ccb" / "ccbd" / "keeper.runtime.log"
    text = runtime_log.read_text(encoding="utf-8")
    assert "keeper-routing-probe" in text


def test_configure_ccbd_logging_honors_log_level_env(tmp_path: Path, monkeypatch) -> None:
    log_path = tmp_path / "ccbd" / "runtime.log"
    logger = logging.getLogger("ccbd.services.example.debug")
    monkeypatch.setenv("CCB_LOG_LEVEL", "DEBUG")

    configure_ccbd_logging(log_path)
    logger.debug("debug-routing-probe")

    assert "debug-routing-probe" in log_path.read_text(encoding="utf-8")


def test_diagnostics_sources_include_keeper_runtime_log(tmp_path: Path) -> None:
    context = SimpleNamespace(paths=PathLayout(tmp_path))

    sources = project_root_sources(context)

    assert ('ccbd-log', context.paths.ccbd_dir / "keeper.runtime.log") in sources
