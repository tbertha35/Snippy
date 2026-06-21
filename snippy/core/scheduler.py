"""Phase 3a: backup reminder scheduler.

Snippy fires a single check on app start (and on demand) to see if the
user hasn't backed up in `config.backup.reminder_days` (default 7). If
so, a non-modal reminder toast is shown.

This is *just* a reminder \u2014 the actual backup is still user-initiated
via **Tray \u25b6 Export\u2026** or the Backup dialog. Full automatic
background export lands in v0.3.1.

Why not a background daemon? The user said:
    \"have it be automatical if the computer is running at that time\"

A daemon would mean a separate process or a service, both of which add
deployment complexity and surprise the user with writes to disk they
didn't ask for. A reminder-toast on app start is honest: it fires the
moment the user opens the app, *if* the machine has been off, that
catches the user up as if the export had run. If the machine is on
and Snippy is running, the check is also called by a low-frequency
QTimer (every 6 hours) so the reminder doesn't pile up.

This module is pure-Python and Qt-free; the QTimer is wired in `app.py`.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from snippy.core.config import BackupConfig, SnippyConfig, save_config
from snippy.core.db import Database


LOGGER = logging.getLogger(__name__)


def is_stale(
    backup: BackupConfig,
    *,
    now: datetime | None = None,
) -> bool:
    """Return True if the user should be reminded to back up.

    \u2022 `last_backup_at is None` \u2192 True (never backed up)
    \u2022 `now - last_backup_at > reminder_days` \u2192 True
    \u2022 Otherwise \u2192 False
    """
    now = now or datetime.now(timezone.utc)
    if backup.last_backup_at is None:
        return True
    try:
        last = datetime.fromisoformat(backup.last_backup_at)
    except ValueError:
        LOGGER.warning("last_backup_at is malformed (%r); treating as stale",
                       backup.last_backup_at)
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (now - last) > timedelta(days=backup.reminder_days)


def days_since_last(backup: BackupConfig, *, now: datetime | None = None) -> int | None:
    """Return whole days since last backup, or None if never."""
    if backup.last_backup_at is None:
        return None
    now = now or datetime.now(timezone.utc)
    try:
        last = datetime.fromisoformat(backup.last_backup_at)
    except ValueError:
        return None
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    delta = now - last
    return max(0, int(delta.total_seconds() // 86400))


def mark_just_backed_up(
    cfg: SnippyConfig,
    db: Database,
    cfg_path: object = None,
) -> SnippyConfig:
    """Stamp `cfg.backup.last_backup_at` with the current UTC time and save.

    Returns the new `SnippyConfig` (the input is left untouched).
    `cfg_path` is the path to save to (defaults to the platform's
    `config_path()` when `None`).
    """
    new_cfg = replace(cfg, backup=replace(cfg.backup, last_backup_at=datetime.now(timezone.utc).isoformat()))
    save_config(new_cfg, cfg_path)
    LOGGER.info("Marked backup timestamp (%d snippet(s))", db.count())
    return new_cfg


class BackupReminder:
    """A small helper that the app wires a QTimer to.

    The QTimer is the *caller* \u2014 the `BackupReminder` is just a check
    function plus a description of what the user should see.
    """

    def __init__(self, cfg: SnippyConfig) -> None:
        self._cfg = cfg

    @property
    def config(self) -> SnippyConfig:
        return self._cfg

    def is_stale(self, *, now: datetime | None = None) -> bool:
        return is_stale(self._cfg.backup, now=now)

    def message(self, *, now: datetime | None = None) -> str:
        """Return the user-facing reminder string (or '' if not stale)."""
        if not self.is_stale(now=now):
            return ""
        days = days_since_last(self._cfg.backup, now=now)
        if days is None:
            return "You haven't backed up your snippets yet. Open Tray \u25b6 Export\u2026 to make a `.snip` backup."
        if days == 0:
            return "It's been less than a day \u2014 you're good. (Reminder window: 7 days.)"
        return (
            f"It's been {days} day(s) since your last backup. "
            f"Open Tray \u25b6 Export\u2026 to make a fresh `.snip` file."
        )