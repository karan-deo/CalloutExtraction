"""Shared logging setup for the annotation tool's entrypoints.

Both ``app.py`` and ``preprocess.py`` call :func:`setup_logging` from their
``__main__`` blocks (never at import time, so importing ``preprocess`` as a
library does not reconfigure the root logger). Logs are written to stderr and a
rotating file under ``logs/``; the level is taken from the ``level`` argument,
else the ``LOG_LEVEL`` environment variable, else ``INFO``.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_LOG_DIR = _ROOT / "logs"
_LOG_FILE = _LOG_DIR / "app.log"

_FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] (%(processName)s) %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Marker so repeated calls (e.g. Flask's reloader spawning a child, or app.py
# importing preprocess and both wanting logs) reconfigure cleanly instead of
# stacking duplicate handlers.
_CONFIGURED_FLAG = "_callout_logging_configured"


def _resolve_level(level: str | int | None) -> int:
    """Turn an explicit level, the LOG_LEVEL env, or the INFO default into a number."""
    raw = level if level is not None else os.environ.get("LOG_LEVEL", "INFO")
    if isinstance(raw, int):
        return raw
    resolved = logging.getLevelName(str(raw).upper())
    # getLevelName returns the string "Level X" for unknown names rather than raising.
    return resolved if isinstance(resolved, int) else logging.INFO


def setup_logging(level: str | int | None = None) -> None:
    """Configure root logging with a console and rotating-file handler.

    Idempotent: a second call replaces the handlers we installed previously
    rather than adding more, so reloads and repeated entrypoint calls do not
    produce duplicate log lines.
    """
    root = logging.getLogger()
    log_level = _resolve_level(level)
    root.setLevel(log_level)

    # Drop handlers we installed on a prior call; leave any foreign handlers be.
    for handler in list(root.handlers):
        if getattr(handler, _CONFIGURED_FLAG, False):
            root.removeHandler(handler)
            handler.close()

    formatter = logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    setattr(console, _CONFIGURED_FLAG, True)
    root.addHandler(console)

    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        _LOG_FILE, maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    setattr(file_handler, _CONFIGURED_FLAG, True)
    root.addHandler(file_handler)

    # Let werkzeug's access logs flow through our handlers/format via propagation
    # instead of its own basicConfig-style handler.
    werkzeug = logging.getLogger("werkzeug")
    werkzeug.handlers.clear()
    werkzeug.propagate = True

    logging.getLogger(__name__).debug(
        "Logging configured at %s -> console + %s",
        logging.getLevelName(log_level),
        _LOG_FILE,
    )
