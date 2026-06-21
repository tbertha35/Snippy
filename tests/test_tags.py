"""Tests for the WS6 tag CRUD on `core.db.Database`."""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


def _make_db(tmp_path: Path):
    from snippy.core.db import Database
    return Database(path=tmp_path / "t.db")


def _add(db, content: str, type_: str = "text") -> int:
    s = db.add_snippet(content, type_)
    assert s is not None
    return s.id


# ---------------------------------------------------------------------------
# add_tag
# ---------------------------------------------------------------------------


def test_add_tag_creates_new(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    try:
        tag_id = db.add_tag("work")
        assert isinstance(tag_id, int) and tag_id > 0
    finally:
        db.close()


def test_add_tag_is_idempotent(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    try:
        a = db.add_tag("work")
        b = db.add_tag("work")
        assert a == b
    finally:
        db.close()


def test_add_tag_case_insensitive(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    try:
        a = db.add_tag("Work")
        b = db.add_tag("WORK")
        c = db.add_tag("work")
        assert a == b == c
    finally:
        db.close()


def test_add_tag_trims_whitespace(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    try:
        a = db.add_tag("work")
        b = db.add_tag("  work  ")
        assert a == b
    finally:
        db.close()


def test_add_tag_rejects_empty(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    try:
        with pytest.raises(ValueError):
            db.add_tag("   ")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# list_tags
# ---------------------------------------------------------------------------


def test_list_tags_empty(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    try:
        assert db.list_tags() == []
    finally:
        db.close()


def test_list_tags_with_counts(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    try:
        s1 = _add(db, "a")
        s2 = _add(db, "b")
        s3 = _add(db, "c")
        db.set_tags_for_snippet(s1, ["work", "important"])
        db.set_tags_for_snippet(s2, ["work"])
        db.set_tags_for_snippet(s3, ["personal"])
        tags = {t["name"]: t["count"] for t in db.list_tags()}
        assert tags == {"work": 2, "important": 1, "personal": 1}
    finally:
        db.close()


def test_list_tags_sorted_by_count_then_name(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    try:
        a = _add(db, "a")
        b = _add(db, "b")
        c = _add(db, "c")
        db.set_tags_for_snippet(a, ["work"])
        db.set_tags_for_snippet(b, ["work"])
        db.set_tags_for_snippet(c, ["archive"])
        names = [t["name"] for t in db.list_tags()]
        assert names[0] == "work"  # most used first
        assert names[-1] == "archive"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# get_tags_for_snippet
# ---------------------------------------------------------------------------


def test_get_tags_for_snippet_empty(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    try:
        sid = _add(db, "x")
        assert db.get_tags_for_snippet(sid) == []
    finally:
        db.close()


def test_get_tags_for_snippet_returns_sorted_names(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    try:
        sid = _add(db, "x")
        db.set_tags_for_snippet(sid, ["zeta", "alpha", "mu"])
        assert db.get_tags_for_snippet(sid) == ["alpha", "mu", "zeta"]
    finally:
        db.close()


# ---------------------------------------------------------------------------
# set_tags_for_snippet
# ---------------------------------------------------------------------------


def test_set_tags_replaces_existing(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    try:
        sid = _add(db, "x")
        db.set_tags_for_snippet(sid, ["a", "b"])
        db.set_tags_for_snippet(sid, ["c"])
        assert db.get_tags_for_snippet(sid) == ["c"]
    finally:
        db.close()


def test_set_tags_empty_clears(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    try:
        sid = _add(db, "x")
        db.set_tags_for_snippet(sid, ["a", "b"])
        db.set_tags_for_snippet(sid, [])
        assert db.get_tags_for_snippet(sid) == []
    finally:
        db.close()


def test_set_tags_normalizes_case_and_dedupes(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    try:
        sid = _add(db, "x")
        db.set_tags_for_snippet(sid, ["Work", "WORK", "  work", "personal"])
        assert db.get_tags_for_snippet(sid) == ["personal", "work"]
    finally:
        db.close()


def test_set_tags_autocreates_unknown(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    try:
        sid = _add(db, "x")
        db.set_tags_for_snippet(sid, ["brand-new-tag"])
        assert db.get_tags_for_snippet(sid) == ["brand-new-tag"]
        # The tag should now be in list_tags
        assert any(t["name"] == "brand-new-tag" for t in db.list_tags())
    finally:
        db.close()


# ---------------------------------------------------------------------------
# add_tag_to_snippet
# ---------------------------------------------------------------------------


def test_add_tag_to_snippet_preserves_existing(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    try:
        sid = _add(db, "x")
        db.set_tags_for_snippet(sid, ["a"])
        db.add_tag_to_snippet(sid, "b")
        assert set(db.get_tags_for_snippet(sid)) == {"a", "b"}
    finally:
        db.close()


def test_add_tag_to_snippet_idempotent(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    try:
        sid = _add(db, "x")
        db.add_tag_to_snippet(sid, "dup")
        db.add_tag_to_snippet(sid, "dup")
        assert db.get_tags_for_snippet(sid) == ["dup"]
    finally:
        db.close()


# ---------------------------------------------------------------------------
# tags_by_snippet
# ---------------------------------------------------------------------------


def test_tags_by_snippet_returns_full_mapping(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    try:
        s1 = _add(db, "a")
        s2 = _add(db, "b")
        s3 = _add(db, "c")
        db.set_tags_for_snippet(s1, ["work", "important"])
        db.set_tags_for_snippet(s2, ["work"])
        # s3 has no tags
        mapping = db.tags_by_snippet()
        assert mapping[s1] == {"work", "important"}
        assert mapping[s2] == {"work"}
        assert s3 not in mapping
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Cascade
# ---------------------------------------------------------------------------


def test_delete_snippet_cascades_to_tag_links(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    try:
        s1 = _add(db, "a")
        s2 = _add(db, "b")
        db.set_tags_for_snippet(s1, ["work", "shared"])
        db.set_tags_for_snippet(s2, ["shared"])
        db.delete_snippet(s1)
        # The tag should still exist (used by s2)
        assert any(t["name"] == "shared" for t in db.list_tags())
        assert any(t["name"] == "work" for t in db.list_tags())
        # And s1 no longer has any tags
        assert db.get_tags_for_snippet(s1) == []
        # s2 still does
        assert db.get_tags_for_snippet(s2) == ["shared"]
    finally:
        db.close()