"""Tests for the Settings dialog (Phase 2 WS1)."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from snippy.ui.settings import _PAGE_BACKUP


def _backup_page_index() -> int:
    """Backup page index in the settings stacked widget (platform-aware)."""
    return 3 if sys.platform == "darwin" else _PAGE_BACKUP


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    app = QApplication.instance() or QApplication(sys.argv)
    return app


@pytest.fixture(autouse=True)
def _isolated_autostart(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the autostart module to use the Linux branch in a temp dir."""
    import snippy.core.autostart as mod
    monkeypatch.setattr(mod, "_current_platform_branch", lambda: "linux")
    tmp = tempfile.mkdtemp(prefix="snippy-settings-")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(Path(tmp) / "config"))


def test_dialog_opens_with_default_config(qt_app: QApplication) -> None:
    from snippy.core.config import SnippyConfig
    from snippy.ui.settings import SettingsDialog

    cfg = SnippyConfig()
    dlg = SettingsDialog(cfg)
    assert dlg.windowTitle().startswith("Snippy")
    # The Hotkeys page (and its editor) is hidden on macOS.
    if sys.platform != "darwin":
        assert dlg._edt_hotkey.text() == cfg.hotkey
    assert dlg._chk_capture_enabled.isChecked() is True
    assert dlg._rad_midnight.isChecked() is True


def test_dialog_collects_edits(qt_app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from snippy.core import config as cfg_mod
    from snippy.core.config import SnippyConfig
    from snippy.ui.settings import SettingsDialog

    # Point Snippy's config_path() at our temp file so _on_apply writes there
    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(cfg_mod, "config_path", lambda: cfg_path)

    cfg = SnippyConfig()
    dlg = SettingsDialog(cfg)
    if sys.platform != "darwin":
        dlg._edt_hotkey.setText("Alt+P")
    dlg._chk_sound.setChecked(True)
    dlg._rad_solarized.setChecked(True)
    dlg._spn_max_length.setValue(12345)

    dlg._on_apply()

    import json
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    if sys.platform != "darwin":
        assert raw["hotkey"] == "Alt+P"
    assert raw["feedback"]["sound"] is True
    assert raw["theme"] == "solarized-dark"
    assert raw["capture"]["max_snippet_length"] == 12345


def test_dialog_emits_config_changed_on_apply(qt_app: QApplication) -> None:
    from snippy.core.config import SnippyConfig
    from snippy.ui.settings import SettingsDialog

    cfg = SnippyConfig()
    dlg = SettingsDialog(cfg)

    captured: list[SnippyConfig] = []
    dlg.config_changed.connect(captured.append)

    dlg._chk_sound.setChecked(True)
    dlg._on_apply()

    assert len(captured) == 1
    assert captured[0].feedback.sound is True


def test_dialog_current_config_reflects_edits(qt_app: QApplication) -> None:
    from snippy.core.config import SnippyConfig
    from snippy.ui.settings import SettingsDialog

    cfg = SnippyConfig()
    dlg = SettingsDialog(cfg)
    dlg._chk_sound.setChecked(True)
    dlg._rad_solarized.setChecked(True)

    cur = dlg.current_config()
    assert cur.feedback.sound is True
    assert cur.theme == "solarized-dark"


def test_nav_has_five_pages(qt_app: QApplication) -> None:
    from snippy.core.config import SnippyConfig
    from snippy.ui.settings import SettingsDialog, _PAGE_BACKUP

    cfg = SnippyConfig()
    dlg = SettingsDialog(cfg)
    # macOS hides the Hotkeys page, so only 4 nav items; other platforms have 5.
    expected_pages = 4 if sys.platform == "darwin" else 5
    assert dlg._nav.count() == expected_pages
    # Switch to the backup page (last) and confirm the stacked widget follows.
    # On macOS the Backup page is at index 3 because Hotkeys is omitted.
    backup_page = 3 if sys.platform == "darwin" else _PAGE_BACKUP
    dlg._nav.setCurrentRow(backup_page)
    assert dlg._pages.currentIndex() == backup_page


def test_autostart_toggle_applies(qt_app: QApplication) -> None:
    """Checking 'Launch at login' calls autostart.enable()."""
    from snippy.core import autostart
    from snippy.core.config import SnippyConfig
    from snippy.ui.settings import SettingsDialog

    autostart.disable()
    assert autostart.is_enabled() is False

    cfg = SnippyConfig()
    dlg = SettingsDialog(cfg)
    dlg._chk_autostart.setChecked(True)
    dlg._on_apply()

    assert autostart.is_enabled() is True
    autostart.disable()  # cleanup


# ---------------------------------------------------------------------------
# Phase 3a / v0.3.0 — Backup tab
# ---------------------------------------------------------------------------


def test_backup_page_builds_with_all_controls(qt_app: QApplication) -> None:
    """The Backup tab is populated, not the v0.2.0 placeholder."""
    from snippy.core.config import SnippyConfig
    from snippy.ui.settings import SettingsDialog

    dlg = SettingsDialog(SnippyConfig())
    # Switch to the Backup page (index 4 on Win/Linux, 3 on macOS)
    dlg._pages.setCurrentIndex(_backup_page_index())
    # All 3 controls must be present
    assert dlg._spn_reminder_days is not None
    assert dlg._lbl_last_backup is not None
    assert dlg._lbl_snippet_count is not None
    # Reminder days clamps to 1-90
    assert dlg._spn_reminder_days.minimum() == 1
    assert dlg._spn_reminder_days.maximum() == 90
    dlg.deleteLater()


def test_backup_page_populates_from_config(qt_app: QApplication) -> None:
    """A non-default BackupConfig shows up in the spinner + status labels."""
    from snippy.core.config import BackupConfig, SnippyConfig
    from snippy.ui.settings import SettingsDialog
    from datetime import datetime, timezone, timedelta

    three_days_ago = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    cfg = SnippyConfig(backup=BackupConfig(
        last_backup_at=three_days_ago,
        reminder_days=21,
    ))
    dlg = SettingsDialog(cfg)
    dlg._pages.setCurrentIndex(_backup_page_index())
    # Spinner shows 21
    assert dlg._spn_reminder_days.value() == 21
    # Status label includes the timestamp + a day count
    assert three_days_ago in dlg._lbl_last_backup.text()
    assert "3 days ago" in dlg._lbl_last_backup.text()
    dlg.deleteLater()


def test_backup_page_shows_never_when_no_backup(qt_app: QApplication) -> None:
    """If last_backup_at is None, the label says 'never'."""
    from snippy.core.config import SnippyConfig
    from snippy.ui.settings import SettingsDialog

    dlg = SettingsDialog(SnippyConfig())  # default: last_backup_at=None
    dlg._pages.setCurrentIndex(_backup_page_index())
    assert "never" in dlg._lbl_last_backup.text().lower()
    dlg.deleteLater()


def test_backup_page_round_trip_through_apply(qt_app: QApplication, tmp_path: Path, monkeypatch) -> None:
    """Editing the reminder spinner + Apply updates the returned config."""
    from snippy.core import config as cfg_mod
    from snippy.core.config import SnippyConfig
    from snippy.ui.settings import SettingsDialog

    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(cfg_mod, "config_path", lambda: cfg_path)

    dlg = SettingsDialog(SnippyConfig())
    dlg._pages.setCurrentIndex(_backup_page_index())
    dlg._spn_reminder_days.setValue(14)
    dlg._on_apply()
    reloaded = cfg_mod.load_config(cfg_path)
    assert reloaded.backup.reminder_days == 14
    dlg.deleteLater()


def test_backup_page_shows_snippet_count_from_db(qt_app: QApplication, tmp_path: Path) -> None:
    """The 'Snippets stored' label reflects the live db (via db_provider)."""
    from snippy.core.config import SnippyConfig
    from snippy.core.db import Database
    from snippy.ui.settings import SettingsDialog

    db_path = tmp_path / "x.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(path=db_path)
    db.add_snippet("one", "text")
    db.add_snippet("two", "text")
    db.add_snippet("three", "url")

    dlg = SettingsDialog(SnippyConfig(), db_provider=lambda: db)
    dlg._pages.setCurrentIndex(_backup_page_index())
    assert "3" in dlg._lbl_snippet_count.text()
    dlg.deleteLater()


def test_backup_page_dash_when_no_db_provider(qt_app: QApplication) -> None:
    """Without a db_provider, the snippet-count line shows '—' (not a crash)."""
    from snippy.core.config import SnippyConfig
    from snippy.ui.settings import SettingsDialog

    dlg = SettingsDialog(SnippyConfig())  # no db_provider
    dlg._pages.setCurrentIndex(_backup_page_index())
    assert dlg._lbl_snippet_count.text() == "\u2014"
    dlg.deleteLater()
