"""Structured logging for Snippy.

Configures a root logger that:
- Writes to stderr (level controlled by --debug)
- Writes to ~/.snippy/snippy.log (rotating, ~1 MB × 3 backups)

The file logger captures INFO+ always, so a user can `tail` it for support.
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from platformdirs import user_log_dir


APP_NAME = "Snippy"
LOG_FILENAME = "snippy.log"


def _log_dir() -> Path:
    """Return (and create) the platform-appropriate log directory."""
    path = Path(user_log_dir(APP_NAME, appauthor=APP_NAME))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _file_formatter() -> logging.Formatter:
    return logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _console_formatter() -> logging.Formatter:
    # Slightly nicer-looking for human eyes.
    return logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )


def setup_logging(*, debug: bool = False) -> None:
    """Initialize Snippy's logging.

    Safe to call multiple times — existing handlers are replaced so re-invocations
    (e.g. during tests) don't double-log.
    """
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)

    # Wipe any previously-installed handlers (so we don't double-log).
    for h in list(root.handlers):
        root.removeHandler(h)

    # --- Console (stderr) -------------------------------------------------
    console = logging.StreamHandler(stream=sys.stderr)
    console.setLevel(logging.DEBUG if debug else logging.INFO)
    console.setFormatter(_console_formatter())
    root.addHandler(console)

    # --- File (rotating) --------------------------------------------------
    try:
        log_path = _log_dir() / LOG_FILENAME
        file_handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=1_000_000,  # ~1 MB
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(_file_formatter())
        root.addHandler(file_handler)
    except OSError as exc:  # pragma: no cover — extremely rare
        # Don't crash the app if the log dir is unwritable; fall back to stderr only.
        logging.getLogger(__name__).warning(
            "Could not open log file: %s. Falling back to stderr only.", exc
        )

    # Quiet down a few chatty third-party loggers.
    logging.getLogger("PySide6").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)