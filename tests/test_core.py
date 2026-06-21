"""Unit tests for the core (non-Qt) modules of Snippy —” Phase 1.

These don't require a display. They cover:
- config load/save round-trip
- Database dedup + stats
- content type detection
- fuzzy search (exact + typo-tolerant)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from snippy.core.config import load_config, save_config
from snippy.core.db import Database, Snippet
from snippy.core.detector import (
    TYPE_EMAIL,
    TYPE_PATH,
    TYPE_TEXT,
    TYPE_URL,
    detect,
)
from snippy.core.search import search


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_config_round_trip(tmp_path: Path) -> None:
    from snippy.core.config import SnippyConfig

    p = tmp_path / "config.json"
    cfg = load_config(p)
    # The default hotkey is platform-specific (Ctrl+Space on Win/Linux,
    # Ctrl+Shift+Space on macOS to avoid the input-source switcher).
    assert cfg.hotkey == SnippyConfig().hotkey
    assert cfg.sync.api_token, "api_token should be auto-generated"
    save_config(cfg, p)
    cfg2 = load_config(p)
    assert cfg2.hotkey == SnippyConfig().hotkey
    assert cfg2.sync.api_token == cfg.sync.api_token


def test_config_partial_override(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    p.write_text('{"hotkey": "Ctrl+Shift+V", "capture": {"enabled": false}}', encoding="utf-8")
    cfg = load_config(p)
    assert cfg.hotkey == "Ctrl+Shift+V"
    assert cfg.capture.enabled is False
    # Unspecified keys keep their defaults
    assert cfg.capture.max_snippet_length == 50_000
    assert cfg.feedback.toast is True


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


def _make_snippet(content: str, type_: str = "text", id_: int = 0) -> Snippet:
    """Tiny helper to build a Snippet for tests without going through DB."""
    from snippy.core.db import hash_content

    return Snippet(
        id=id_,
        content=content,
        content_hash=hash_content(content),
        content_type=type_,
        source_app=None,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        last_used_at=None,
        use_count=0,
        pin_order=id_,
        is_pinned=False,
        is_archived=False,
        is_sensitive=False,
    )


def test_db_dedup(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.db")
    try:
        s1 = db.add_snippet("hello world", "text")
        s2 = db.add_snippet("hello world", "text")  # duplicate
        s3 = db.add_snippet("foo bar", "text")
        assert s1 is not None and s2 is not None and s3 is not None
        assert s1.id == s2.id, "duplicate should return the same id"
        assert s3.id != s1.id
        assert db.count() == 2
    finally:
        db.close()


def test_db_stats(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.db")
    try:
        db.add_snippet("a", "text")
        db.add_snippet("b", "text")
        db.add_snippet("c", "text")
        stats = db.stats()
        assert stats["total"] == 3
        assert stats["active"] == 3
        assert stats["pinned"] == 0
    finally:
        db.close()


def test_db_bump_used_updates_count_and_time(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.db")
    try:
        s = db.add_snippet("hello world", "text")
        assert s is not None
        assert s.use_count == 0
        assert s.last_used_at is not None
        last = s.last_used_at
        db.bump_used(s.id)
        refreshed = db.get_by_id(s.id)
        assert refreshed is not None
        assert refreshed.use_count == 1
        assert refreshed.last_used_at is not None
        assert refreshed.last_used_at != last
    finally:
        db.close()


def test_db_pin_order(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.db")
    try:
        s1 = db.add_snippet("a", "text")
        s2 = db.add_snippet("b", "text")
        assert s1 is not None and s2 is not None
        db.set_pinned(s1.id, True)
        db.set_pinned(s2.id, True)
        # Newly-pinned snippets get ascending pin_order
        p1 = db.get_by_id(s1.id)
        p2 = db.get_by_id(s2.id)
        assert p1 is not None and p2 is not None
        assert p1.pin_order < p2.pin_order
        db.set_pin_order(s1.id, 99)
        assert db.get_by_id(s1.id).pin_order == 99
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content,expected",
    [
        ("https://github.com/tbertha35/snippy", TYPE_URL),
        ("http://example.com/foo?bar=1", TYPE_URL),
        ("foo.bar@example.com", TYPE_EMAIL),
        ("C:\\Users\\you\\file.txt", TYPE_PATH),
        ("/home/you/notes.md", TYPE_PATH),
        ("just some plain text", TYPE_TEXT),
        ("", TYPE_TEXT),
    ],
)
def test_detect_basic(content: str, expected: str) -> None:
    assert detect(content).type == expected


def test_detect_url_takes_priority_over_email() -> None:
    # An email inside a URL-shaped string should be classified as URL.
    assert detect("https://user@example.com").type == TYPE_URL


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def test_search_exact(tmp_path: Path) -> None:
    snips = [
        _make_snippet("https://github.com/tbertha35/snippy", "url", 1),
        _make_snippet("hello world from pytest", "text", 2),
        _make_snippet("gthub tken", "text", 3),
    ]
    hits = search("github", snips)
    assert hits, "should find at least one hit"
    assert hits[0].snippet.id == 1
    assert hits[0].score > 0


def test_search_fuzzy_typo(tmp_path: Path) -> None:
    snips = [
        _make_snippet("https://github.com/tbertha35/snippy", "url", 1),
        _make_snippet("hello world", "text", 2),
        _make_snippet("github token xyz", "text", 3),
    ]
    # Typo "gthub" (missing 'i') should still rank github-related snippets highly.
    # Pass use_frecency=False to isolate the pure-fuzzy behavior (frecency
    # with zero use_count on test data can suppress a borderline fuzzy match).
    hits = search("gthub", snips, use_frecency=False)
    assert hits
    top_ids = {h.snippet.id for h in hits}
    # Both the URL (id=1) and the text "github token xyz" (id=3) should appear
    assert 1 in top_ids, "github URL should match a typo'd 'gthub'"
    assert 3 in top_ids, "github text should match a typo'd 'gthub'"
    # Either #1 or #3 (or both) is the top hit
    assert hits[0].snippet.id in (1, 3)


def test_search_empty_query_returns_recents(tmp_path: Path) -> None:
    snips = [
        _make_snippet("a", "text", 1),
        _make_snippet("b", "text", 2),
        _make_snippet("c", "text", 3),
    ]
    hits = search("", snips)
    assert len(hits) == 3
    # All hits should have a neutral score for empty query
    assert all(h.score == 50.0 for h in hits)


def test_search_min_score_filters_garbage(tmp_path: Path) -> None:
    snips = [_make_snippet("https://github.com/tbertha35/snippy", "url", 1)]
    # "zzz" should not match anything with the default threshold
    hits = search("zzz", snips)
    assert hits == []
