"""
Standard logging setup — one place that wires up handlers, levels, format.

Call configure_logging() once at process start (FastAPI startup, CLI entrypoint,
or test bootstrap). Subsequent calls are no-ops.

Format: ISO-8601 UTC timestamp · level · logger · message.
Noisy third-party libs (botocore, urllib3, httpx wire-level) are pinned to WARNING.
"""
from __future__ import annotations

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from app.config import settings

_LOCK = threading.Lock()
_CONFIGURED = False

_FORMAT = "%(asctime)s %(levelname)-7s %(name)-30s %(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%S%z"


def configure_logging(
    level: Optional[str] = None,
    log_file: Optional[str] = None,
) -> None:
    """Idempotent setup. Safe to call from multiple entrypoints."""
    global _CONFIGURED
    with _LOCK:
        if _CONFIGURED:
            return
        lvl = (level or settings.log_level or "INFO").upper()
        root = logging.getLogger()
        root.setLevel(lvl)

        # Replace whatever uvicorn/fastapi set up — we want one consistent handler.
        for h in list(root.handlers):
            root.removeHandler(h)

        formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        root.addHandler(stream)

        if log_file:
            Path(log_file).parent.mkdir(parents=True, exist_ok=True)
            fh = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=5)
            fh.setFormatter(formatter)
            root.addHandler(fh)

        # Tame noisy libs
        for noisy in ("botocore", "boto3", "s3transfer", "urllib3", "httpcore"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

        # uvicorn has its own access logger; align format
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
            lg = logging.getLogger(name)
            lg.handlers.clear()
            lg.propagate = True
            lg.setLevel(lvl)

        _CONFIGURED = True
        logging.getLogger(__name__).info(
            "logging configured: level=%s file=%s", lvl, log_file or "(stderr only)"
        )
