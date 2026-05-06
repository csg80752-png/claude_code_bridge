from __future__ import annotations

import logging
from pathlib import Path

from ccbd.runtime import write_log


class CcbdRuntimeLogHandler(logging.Handler):
    def __init__(self, path: Path) -> None:
        super().__init__(level=logging.WARNING)
        self.path = Path(path)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            write_log(self.path, message)
        except Exception:
            self.handleError(record)


def configure_ccbd_logging(log_path: Path) -> None:
    root = logging.getLogger()
    for handler in tuple(root.handlers):
        if isinstance(handler, CcbdRuntimeLogHandler):
            root.removeHandler(handler)
            handler.close()

    handler = CcbdRuntimeLogHandler(Path(log_path))
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(handler)
    if root.level > logging.WARNING:
        root.setLevel(logging.WARNING)


__all__ = ["configure_ccbd_logging", "CcbdRuntimeLogHandler"]
