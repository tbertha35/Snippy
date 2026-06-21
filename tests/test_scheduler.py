"""Tests for `core.scheduler` (Phase 3a backup reminder)."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


def _now() -> datetime:
    return datetime(2026, 6, 13, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# is_stale
# ---------------------------------------------------------------------------


def test_is_stale_when_never_backed_up() -> None:
    from snippy.core.config import BackupConfig
    from snippy.core.scheduler import is_stale
    assert is_stale(BackupConfig(), now=_now()) is True


def test_is_stale_false_when_recent() -> None:
    from snippy.core.config import BackupConfig
    from snippy.core.scheduler import is_stale
    recent = (_now() - timedelta(days=2)).isoformat()
    assert is_stale(BackupConfig(last_backup_at=recent), now=_now()) is False


def test_is_stale_true_at_7_days() -> None:
    from snippy.core.config import BackupConfig
    from snippy.core.scheduler import is_stale
    eight_days_ago = (_now() - timedelta(days=8)).isoformat()
    assert is_stale(BackupConfig(last_backup_at=eight_days_ago), now=_now()) is True


def test_is_stale_boundary_is_just_under_7_days() -> None:
    """The check is `> reminder_days`, so 7 days is still fresh."""
    from snippy.core.config import BackupConfig
    from snippy.core.scheduler import is_stale
    six_days_ago = (_now() - timedelta(days=6)).isoformat()
    assert is_stale(BackupConfig(last_backup_at=six_days_ago), now=_now()) is False


def test_is_stale_treats_malformed_as_stale() -> None:
    from snippy.core.config import BackupConfig
    from snippy.core.scheduler import is_stale
    assert is_stale(BackupConfig(last_backup_at="not a date"), now=_now()) is True


def test_is_stale_treats_naive_datetime_as_utc() -> None:
    """Forgiving: if the timestamp lacks tz info, treat as UTC."""
    from snippy.core.config import BackupConfig
    from snippy.core.scheduler import is_stale
    naive_old = (_now() - timedelta(days=30)).replace(tzinfo=None).isoformat()
    assert is_stale(BackupConfig(last_backup_at=naive_old), now=_now()) is True


# ---------------------------------------------------------------------------
# days_since_last
# ---------------------------------------------------------------------------


def test_days_since_last_none_when_never() -> None:
    from snippy.core.config import BackupConfig
    from snippy.core.scheduler import days_since_last
    assert days_since_last(BackupConfig(), now=_now()) is None


def test_days_since_last_rounds_down() -> None:
    from snippy.core.config import BackupConfig
    from snippy.core.scheduler import days_since_last
    three_days = (_now() - timedelta(days=3, hours=23)).isoformat()
    assert days_since_last(BackupConfig(last_backup_at=three_days), now=_now()) == 3


# ---------------------------------------------------------------------------
# mark_just_backed_up
# ---------------------------------------------------------------------------


def test_mark_just_backed_up_stamps_timestamp(tmp_path: Path) -> None:
    from snippy.core.config import SnippyConfig
    from snippy.core.db import Database
    from snippy.core.scheduler import mark_just_backed_up
    db_path = tmp_path / "x.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(path=db_path)
    db.add_snippet("hello", "text")

    cfg = SnippyConfig()
    assert cfg.backup.last_backup_at is None
    cfg_path = tmp_path / "config.json"
    new_cfg = mark_just_backed_up(cfg, db, cfg_path=cfg_path)

    assert new_cfg.backup.last_backup_at is not None
    # And it round-trips through save_config
    from snippy.core.config import load_config
    reloaded = load_config(cfg_path)
    assert reloaded.backup.last_backup_at is not None


def test_mark_just_backed_up_does_not_mutate_input(tmp_path: Path) -> None:
    from snippy.core.config import SnippyConfig
    from snippy.core.db import Database
    from snippy.core.scheduler import mark_just_backed_up
    db_path = tmp_path / "x.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(path=db_path)
    db.add_snippet("x", "text")
    cfg = SnippyConfig()
    before = cfg.backup.last_backup_at
    _ = mark_just_backed_up(cfg, db, cfg_path=tmp_path / "c.json")
    assert cfg.backup.last_backup_at == before  # unchanged


# ---------------------------------------------------------------------------
# BackupReminder
# ---------------------------------------------------------------------------


def test_reminder_message_when_never() -> None:
    from snippy.core.config import SnippyConfig
    from snippy.core.scheduler import BackupReminder
    r = BackupReminder(SnippyConfig())
    msg = r.message(now=_now())
    assert "haven't backed up" in msg or "Tray" in msg


def test_reminder_message_includes_day_count() -> None:
    from snippy.core.config import BackupConfig, SnippyConfig
    from snippy.core.scheduler import BackupReminder
    ten_days_ago = (_now() - timedelta(days=10)).isoformat()
    cfg = SnippyConfig(backup=BackupConfig(last_backup_at=ten_days_ago))
    r = BackupReminder(cfg)
    msg = r.message(now=_now())
    assert "10 day" in msg


def test_reminder_message_empty_when_recent() -> None:
    from snippy.core.config import BackupConfig, SnippyConfig
    from snippy.core.scheduler import BackupReminder
    recent = (_now() - timedelta(days=1)).isoformat()
    cfg = SnippyConfig(backup=BackupConfig(last_backup_at=recent))
    r = BackupReminder(cfg)
    assert r.message(now=_now()) == ""


def test_reminder_is_stale_delegates() -> None:
    from snippy.core.config import BackupConfig, SnippyConfig
    from snippy.core.scheduler import BackupReminder
    r = BackupReminder(SnippyConfig(backup=BackupConfig()))
    assert r.is_stale(now=_now()) is True
    fresh = BackupReminder(SnippyConfig(backup=BackupConfig(
        last_backup_at=(_now() - timedelta(days=1)).isoformat()
    )))
    assert fresh.is_stale(now=_now()) is False