from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

from ccbd.logging_runtime import configure_ccbd_logging


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
