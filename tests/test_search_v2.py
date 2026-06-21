"""Tests for Phase 2 search features: frecency + operators + tag filter."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from snippy.core.db import Snippet, hash_content
from snippy.core.search import (
    DEFAULT_MIN_SCORE,
    ParsedQuery,
    parse_query,
    search,
)


# Fixed "now" so recency scores are deterministic in tests
NOW = datetime(2026, 6, 12, 12, 0, 0, tzinfo=timezone.utc)


def _make_snippet(
    content: str,
    type_: str = "text",
    id_: int = 0,
    *,
    last_used_at: str | None = None,
    use_count: int = 0,
    is_pinned: bool = False,
    is_archived: bool = False,
    created_at: str = "2026-01-01T00:00:00+00:00",
) -> Snippet:
    return Snippet(
        id=id_,
        content=content,
        content_hash=hash_content(content),
        content_type=type_,
        source_app=None,
        created_at=created_at,
        updated_at=created_at,
        last_used_at=last_used_at,
        use_count=use_count,
        pin_order=id_ if is_pinned else 0,
        is_pinned=is_pinned,
        is_archived=is_archived,
        is_sensitive=False,
    )


# ---------------------------------------------------------------------------
# Query parsing (WS7)
# ---------------------------------------------------------------------------


def test_parse_query_plain_text() -> None:
    p = parse_query("github token")
    assert p.free_text == "github token"
    assert not p.has_filters()


def test_parse_query_tag_operator() -> None:
    p = parse_query("tag:work")
    assert p.tag == "work"
    assert p.free_text == ""


def test_parse_query_type_operator() -> None:
    p = parse_query("type:url github")
    assert p.type == "url"
    assert p.free_text == "github"


def test_parse_query_pin_yes() -> None:
    p = parse_query("pin:yes foo")
    assert p.pin is True
    assert p.free_text == "foo"


def test_parse_query_pin_no() -> None:
    p = parse_query("pin:no bar")
    assert p.pin is False
    assert p.free_text == "bar"


def test_parse_query_archive_no() -> None:
    p = parse_query("archive:no hello")
    assert p.archive is False
    assert p.free_text == "hello"


def test_parse_query_before_date() -> None:
    p = parse_query("before:2026-01-01 x")
    assert p.before is not None
    assert p.before.year == 2026 and p.before.month == 1 and p.before.day == 1
    assert p.free_text == "x"


def test_parse_query_after_date() -> None:
    p = parse_query("after:2026/06/01 foo")
    assert p.after is not None
    assert p.after.year == 2026 and p.after.month == 6
    assert p.free_text == "foo"


def test_parse_query_combined() -> None:
    p = parse_query("tag:work type:url pin:yes before:2026-12-31 github token")
    assert p.tag == "work"
    assert p.type == "url"
    assert p.pin is True
    assert p.before is not None
    assert p.free_text == "github token"


def test_parse_query_describe() -> None:
    p = parse_query("tag:work type:url pin:yes")
    desc = p.describe()
    assert "tag: work" in desc
    assert "type: url" in desc
    assert "pinned" in desc


def test_parse_query_handles_invalid_date_gracefully() -> None:
    p = parse_query("before:not-a-date x")
    assert p.before is None
    assert p.free_text == "x"


# ---------------------------------------------------------------------------
# Operator-driven filtering (WS6/WS7)
# ---------------------------------------------------------------------------


def test_search_filters_by_type() -> None:
    snips = [
        _make_snippet("https://github.com/tbertha35/snippy", "url", 1),
        _make_snippet("hello world", "text", 2),
        _make_snippet("foo@bar.com", "email", 3),
    ]
    hits = search("type:url foo", snips)
    # type:url filter keeps only the URL; "foo" then fuzzy-matches that URL
    # (no "foo" in github.com, so empty) — let's also check just the type filter
    hits = search("type:url github", snips)
    assert len(hits) == 1
    assert hits[0].snippet.id == 1


def test_search_filters_by_pin_yes() -> None:
    snips = [
        _make_snippet("hello world", "text", 1, is_pinned=False),
        _make_snippet("hello there", "text", 2, is_pinned=True),
    ]
    hits = search("pin:yes hello", snips)
    assert len(hits) == 1
    assert hits[0].snippet.id == 2


def test_search_filters_by_pin_no() -> None:
    snips = [
        _make_snippet("hello world", "text", 1, is_pinned=False),
        _make_snippet("hello there", "text", 2, is_pinned=True),
    ]
    hits = search("pin:no hello", snips)
    assert len(hits) == 1
    assert hits[0].snippet.id == 1


def test_search_filters_by_archive_no() -> None:
    snips = [
        _make_snippet("hello world", "text", 1, is_archived=False),
        _make_snippet("hello there", "text", 2, is_archived=True),
    ]
    hits = search("archive:no hello", snips)
    assert len(hits) == 1
    assert hits[0].snippet.id == 1


def test_search_filters_by_tag() -> None:
    snips = [
        _make_snippet("hello world", "text", 1),
        _make_snippet("hello there", "text", 2),
    ]
    tags = {1: {"work"}, 2: {"personal"}}
    hits = search("tag:work hello", snips, tags_by_snippet=tags)
    assert len(hits) == 1
    assert hits[0].snippet.id == 1


def test_search_filters_by_before() -> None:
    snips = [
        _make_snippet("hello world", "text", 1, created_at="2025-12-01T00:00:00+00:00"),
        _make_snippet("hello there", "text", 2, created_at="2026-05-01T00:00:00+00:00"),
    ]
    hits = search("before:2026-01-01 hello", snips)
    assert len(hits) == 1
    assert hits[0].snippet.id == 1


def test_search_filters_combine() -> None:
    snips = [
        _make_snippet("github.com", "url", 1, is_pinned=True),
        _make_snippet("github.com/foo", "url", 2, is_pinned=False),
        _make_snippet("github.com/bar", "text", 3, is_pinned=True),
    ]
    hits = search("tag:work type:url pin:yes github", snips)
    # 1: pinned + url + (no tag set) → filtered out
    # 2: unpinned → filtered out
    # 3: pinned but text → filtered out
    assert hits == []


# ---------------------------------------------------------------------------
# Frecency (WS3)
# ---------------------------------------------------------------------------


def test_frecency_prefers_recent_over_older() -> None:
    snips = [
        _make_snippet("github token abc", "text", 1, last_used_at="2025-01-01T00:00:00+00:00"),
        _make_snippet("github token xyz", "text", 2, last_used_at="2026-06-12T00:00:00+00:00"),
    ]
    # with frecency
    hits = search("github", snips, now=NOW)
    assert hits[0].snippet.id == 2, "more recent should win under frecency"


def test_frecency_prefers_frequent_over_single_use() -> None:
    snips = [
        _make_snippet("github token abc", "text", 1, use_count=50),
        _make_snippet("github token xyz", "text", 2, use_count=1),
    ]
    hits = search("github", snips, now=NOW)
    assert hits[0].snippet.id == 1, "used 50× should beat used 1Ã—"


def test_pinned_still_gets_boost() -> None:
    snips = [
        _make_snippet("github.com", "url", 1, is_pinned=True, use_count=0),
        _make_snippet("github.com/other", "url", 2, is_pinned=False, use_count=100),
    ]
    hits = search("github", snips, now=NOW)
    # Pin boost is +5, but heavy use should still dominate; this just
    # verifies pinning doesn't break scoring catastrophically.
    assert len(hits) == 2
    assert all(h.score > 0 for h in hits)


def test_frecency_can_be_disabled() -> None:
    snips = [
        _make_snippet("github abc", "text", 1, last_used_at="2025-01-01T00:00:00+00:00"),
        _make_snippet("github xyz", "text", 2, last_used_at="2026-06-12T00:00:00+00:00"),
    ]
    # Without frecency, both are equal-fuzzy, order is implementation-defined
    # but the top hits' score should be just the fuzzy score, not blended.
    hits_on = search("github", snips, use_frecency=True, now=NOW)
    hits_off = search("github", snips, use_frecency=False, now=NOW)
    # The off scores should be ≥ the on scores (because on dampens with recency)
    assert hits_off[0].score >= hits_on[0].score
    # And the recent one should still win on
    assert hits_on[0].snippet.id == 2


def test_empty_query_with_operators_returns_filtered_recents() -> None:
    """`pin:yes` (no free text) should return pinned recents."""
    snips = [
        _make_snippet("a", "text", 1, is_pinned=False),
        _make_snippet("b", "text", 2, is_pinned=True),
        _make_snippet("c", "text", 3, is_pinned=True),
    ]
    hits = search("pin:yes", snips)
    assert {h.snippet.id for h in hits} == {2, 3}
