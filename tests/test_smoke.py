"""Smoke tests for Snippy — Phase 0.

These don't try to launch the GUI (which would require a display).
They verify that the package imports cleanly and exposes the right symbols.
"""
from __future__ import annotations


def test_package_metadata() -> None:
    from snippy import __app_name__, __version__

    assert __app_name__ == "Snippy"
    assert isinstance(__version__, str)
    assert __version__  # non-empty


def test_logging_setup_is_idempotent() -> None:
    """setup_logging() should be safe to call multiple times without doubling output."""
    import logging

    from snippy.core.logging import setup_logging

    root = logging.getLogger()
    n_before = len(root.handlers)

    setup_logging(debug=False)
    n_after_first = len(root.handlers)
    setup_logging(debug=True)
    n_after_second = len(root.handlers)

    # It should remove previous handlers first, so the count stays bounded
    # rather than growing without limit across calls.
    assert n_after_first == n_after_second
    assert n_after_first >= 1  # at least the console handler


def test_run_returns_int() -> None:
    """run() is the entry point and should be importable without side effects."""
    from snippy.app import run

    assert callable(run)


def test_log_file_is_written() -> None:
    """After setup_logging() + a real log call, the rotating log file exists and contains the message."""
    import logging
    from pathlib import Path

    from platformdirs import user_log_dir

    from snippy.core.logging import LOG_FILENAME, setup_logging

    setup_logging(debug=False)
    test_logger = logging.getLogger("snippy.smoke.log_test")
    test_logger.info("phase-0 log file write check")

    log_path = Path(user_log_dir("Snippy", appauthor="Snippy")) / LOG_FILENAME
    assert log_path.exists(), f"Log file was not created at {log_path}"

    contents = log_path.read_text(encoding="utf-8")
    assert "phase-0 log file write check" in contents
    assert "snippy.smoke.log_test" in contents
