from __future__ import annotations

import logging
import os
from pathlib import Path

from ccbd.runtime import write_log


def _log_level_from_env() -> int:
    value = os.environ.get('CCB_LOG_LEVEL', 'WARNING').strip().upper()
    if not value:
        return logging.WARNING
    return int(getattr(logging, value, logging.WARNING))


class CcbdRuntimeLogHandler(logging.Handler):
    def __init__(self, path: Path, *, level: int = logging.WARNING) -> None:
        super().__init__(level=level)
        self.path = Path(path)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            write_log(self.path, message)
        except Exception:
            self.handleError(record)


def configure_ccbd_logging(log_path: Path) -> None:
    root = logging.getLogger()
    level = _log_level_from_env()
    for handler in tuple(root.handlers):
        if isinstance(handler, CcbdRuntimeLogHandler):
            root.removeHandler(handler)
            handler.close()

    handler = CcbdRuntimeLogHandler(Path(log_path), level=level)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(handler)
    if root.level > level:
        root.setLevel(level)


__all__ = ["configure_ccbd_logging", "CcbdRuntimeLogHandler"]
