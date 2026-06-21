"""End-to-end smoke test: build the full SnippyApp and exercise its core flow.

Uses Qt's "offscreen" platform so the test runs without a display.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Force offscreen Qt platform BEFORE any Qt import
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    app = QApplication.instance() or QApplication(sys.argv)
    return app


@pytest.fixture()
def fresh_snippy_app(qt_app: QApplication, tmp_path: Path):
    """Build a fresh SnippyApp pointed at a brand-new sandboxed DB file."""
    from snippy.app import SnippyApp
    from snippy.core.config import load_config

    cfg = load_config()
    db_path = tmp_path / "snippy.db"
    snippy = SnippyApp(qt_app, cfg, db_path_override=db_path)
    snippy.start()
    yield snippy
    # Cleanup: ensure the DB connection is fully closed before tmp_path is removed
    try:
        snippy._quit()
    except Exception:
        pass
    qt_app.processEvents()


def test_snippy_app_starts_and_creates_default_config(fresh_snippy_app) -> None:
    snippy = fresh_snippy_app
    assert snippy._tray.icon() is not None

    # Simulate new clipboard captures
    snippy._on_capture("https://example.com/abc")
    snippy._on_capture("hello world this is a test")
    snippy._on_capture("foo@bar.com")

    # The DB should now have 3 snippets
    assert snippy._db.count() == 3
    stats = snippy._db.stats()
    assert stats["active"] == 3
    assert stats["total"] == 3

    # The History window can find them
    all_snips = snippy._db.list_all_for_search()
    assert len(all_snips) == 3


def test_snippy_dedupes_consecutive_copies(fresh_snippy_app) -> None:
    snippy = fresh_snippy_app
    # 3 identical copies →’ only 1 row
    snippy._on_capture("duplicate content")
    snippy._on_capture("duplicate content")
    snippy._on_capture("duplicate content")
    assert snippy._db.count() == 1


def test_snippy_history_activates_snippet(fresh_snippy_app) -> None:
    snippy = fresh_snippy_app
    target = "https://github.com/tbertha35/snippy"
    snippy._on_capture(target)

    snips = snippy._db.list_all_for_search()
    target_snip = next(s for s in snips if s.content == target)
    snippy._on_snippet_activated(target_snip)

    cb_text = snippy._qt.clipboard().text()
    assert cb_text == target


def test_snippy_dedup_returns_same_id(fresh_snippy_app) -> None:
    """A second copy of an existing snippet returns the same id (Phase 1 spec)."""
    snippy = fresh_snippy_app
    a = snippy._on_capture("just one")
    # Manually re-add to verify dedup at the DB level
    second = snippy._db.add_snippet("just one", "text")
    assert second is not None
    # Same content → same id
    assert second.id == 1
