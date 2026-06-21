"""Snippy main application — Phase 1 MVP.

This module wires up:
- Logging (structured, rolling, to platform user log dir)
- The QApplication
- The config layer (loaded once on launch)
- The SQLite database (migrations applied)
- The clipboard watcher (QTimer-driven, 250ms poll)
- The system tray icon (with Pause/Resume, Open Snippy, Quit menu)
- The History window (Ctrl+Space, fuzzy search)
- The feedback bus (toast + tray flash on capture)

Phase 1 exit criteria (from ROADMAP.md):
  Copy text, hit Ctrl+Space, fuzzy-find it, press Enter, paste it
  somewhere else. App lives in tray.
"""
from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import QObject, Qt, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QFont, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from snippy import __app_name__, __version__
from snippy.core.capture_screen import (
    ImageViewer,
    capture_screen_region,
    copy_pixmap_to_clipboard,
    default_image_save_dir,
    resolve_image_save_dir,
)
from snippy.core.clipboard import ClipboardWatcher
from snippy.core.config import SnippyConfig, load_config
from snippy.core.db import Database, Snippet
from snippy.core.detector import detect
from snippy.core.global_hotkey import GlobalHotkey
from snippy.core.logging import setup_logging
from snippy.ui.feedback import FeedbackBus, _build_default_icon
from snippy.ui.styles import stylesheet_for


LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Windows AppUserModelID
# ---------------------------------------------------------------------------
#
# Windows uses the AppUserModelID to (a) group taskbar entries, (b) match
# the Start-menu shortcut to the running process, and (c) decide which
# icon to show in the taskbar / Alt-Tab / task manager. Without an
# explicit ID, a PyInstaller .exe may show a generic blank icon and the
# taskbar entry won't appear at all when the only window is hidden
# (Phase 1: Snippy lives in the tray).
#
# This must be called *before* QApplication() is constructed so the
# process-wide ID is in place by the time the first window is created.

_APP_USER_MODEL_ID = "Snippy.SnippyTray.0.3"


def _set_windows_app_user_model_id() -> None:
    """Pin the AppUserModelID for this process on Windows; no-op elsewhere.

    See https://learn.microsoft.com/en-us/windows/win32/api/shobjidl_core/nf-shobjidl_core-setcurrentprocessexplicitappusermodelid
    for the Win32 details.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            _APP_USER_MODEL_ID
        )
    except Exception:  # pragma: no cover
        # Older Windows (<7) doesn't have shell32.SetCurrentProcessExplicitAppUserModelID.
        # That's fine; Windows will just use the .exe's embedded icon instead.
        pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="snippy",
        description=f"{__app_name__} — smart clipboard & snippet manager",
    )
    parser.add_argument("--debug", action="store_true", help="Enable verbose (DEBUG-level) logging.")
    parser.add_argument(
        "--autostart",
        action="store_true",
        help="Launched by the OS login/startup mechanism (no-op flag, suppressed from help).",
    )
    parser.add_argument("--version", action="version", version=f"{__app_name__} {__version__}")
    return parser.parse_args(list(argv))


# ---------------------------------------------------------------------------
# The "main window" is just a tiny placeholder so Quit is meaningful when
# the user enables quit-on-last-window in the future. In Phase 1 the real
# UI is the tray + History window.
# ---------------------------------------------------------------------------


class PlaceholderWindow(QMainWindow):
    def __init__(self, config: SnippyConfig) -> None:
        super().__init__()
        self.setWindowTitle(f"{__app_name__} {__version__}")
        self.resize(480, 240)
        # Apply the real app icon to the status window's titlebar
        # (the global QApplication.setWindowIcon already handles most
        # platforms, but Windows in particular respects the per-window
        # setWindowIcon for the taskbar preview).
        icon = _build_default_icon()
        if not icon.isNull():
            self.setWindowIcon(icon)
        central = QWidget(self)
        layout = QVBoxLayout(central)
        title = QLabel(f"✂️  {__app_name__} is running", central)
        title.setObjectName("statusTitle")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        # Note: objectName lets the theme stylesheet pick up the color;
        # the previous inline `color: palette(mid)` was invalid QSS and
        # the label ended up with whatever color the parent widget
        # declared — very low contrast on a few themes.
        if sys.platform == "darwin":
            body_text = (
                "Snippy lives in your system tray.\n\n"
                "• Copy anything → it auto-captures (you'll see a toast + tray flash)\n"
                "• Click the tray icon or use the tray menu to open Snippy\n"
                "• Right-click the tray icon for more options"
            )
        else:
            body_text = (
                "Snippy lives in your system tray.\n\n"
                "• Copy anything → it auto-captures (you'll see a toast + tray flash)\n"
                f"• Press {config.hotkey} to open Snippy\n"
                "• Right-click the tray icon for more options"
            )
        body = QLabel(body_text, central)
        body.setObjectName("statusBody")
        body.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(body)
        layout.addStretch(1)
        self.setCentralWidget(central)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


class SnippyApp(QObject):
    """Top-level coordinator. Owns the long-lived objects and the tray menu."""

    def __init__(
        self,
        qt_app: QApplication,
        config: SnippyConfig,
        *,
        db_path_override: Path | None = None,
        config_path_override: Path | None = None,
    ) -> None:
        super().__init__()
        self._qt = qt_app
        self._config = config

        # Storage (test mode lets you point at a private DB file)
        self._db = Database(path=db_path_override) if db_path_override else Database()
        self._db.setParent(self)
        self._db.snippets_changed.connect(self._on_snippets_changed)

        # UI
        self._window = PlaceholderWindow(config)
        # The History window is the main UI now; the palette has been removed.
        # (Windows are top-level so we don't reparent them; holding the
        # attribute on self keeps them alive for the app's lifetime.)
        self._history_window: "HistoryWindow | None" = None
        # WS4: keep a reference to the open detail dialog so it doesn't GC.
        self._detail_dialog = None

        # Settings dialog (Phase 2 WS1) — created on demand via tray menu
        self._settings_dialog = None

        # Feedback
        self._feedback = FeedbackBus(qt_app, parent=self)

        # Clipboard
        self._clipboard = ClipboardWatcher(qt_app, config.capture, parent=self)
        self._clipboard.snippet_captured.connect(self._on_capture)

        # Tray
        self._tray = QSystemTrayIcon(_build_default_icon(), parent=self)
        self._tray.setToolTip(f"{__app_name__} —” 0 snippets")
        self._tray.activated.connect(self._on_tray_activated)
        self._feedback.set_tray(self._tray)
        self._build_tray_menu()

        # Global hotkeys are only supported on Windows and Linux. macOS global
        # hotkeys require Accessibility permissions that are unreliable for an
        # ad-hoc signed .app, so we skip them on macOS and rely on the tray icon
        # + in-app shortcuts instead.
        self._global_hotkey = GlobalHotkey(self)
        self._image_hotkey = GlobalHotkey(self)
        self._fallback_shortcuts: list[QShortcut] = []
        if sys.platform == "darwin":
            self._hotkey_ok = True  # not applicable; avoid warning
        else:
            self._hotkey_ok = self._register_hotkeys(
                self._config.hotkey, self._config.capture.image_hotkey
            )

        # Always keep a status window on the taskbar so users can pin
        # Snippy and alt-tab to it.
        self._window.showNormal()
        self._window.raise_()

    # -- tray menu --------------------------------------------------------

    def _build_tray_menu(self) -> None:
        menu = QMenu()

        if sys.platform == "darwin":
            act_open = QAction("Open Snippy…", self)
            act_capture = QAction("Capture screen region…", self)
        else:
            act_open = QAction(f"Open Snippy…  ({self._config.hotkey})", self)
            act_capture = QAction(f"Capture screen region…  ({self._config.capture.image_hotkey})", self)
        act_open.triggered.connect(self._on_open_history)
        act_capture.triggered.connect(self._on_capture_screen)
        menu.addAction(act_open)
        menu.addAction(act_capture)

        menu.addSeparator()

        # Phase 3a: backup / restore
        act_export = QAction("\U0001f4e4  Export backup…", self)
        act_export.triggered.connect(self._on_export_backup)
        menu.addAction(act_export)
        act_import = QAction("\U0001f4e5  Import backup…", self)
        act_import.triggered.connect(self._on_import_backup)
        menu.addAction(act_import)

        menu.addSeparator()

        act_show = QAction("Show status window", self)
        act_show.triggered.connect(self._window.show)
        menu.addAction(act_show)

        menu.addSeparator()

        self._act_pause = QAction("Pause capture", self, checkable=True)
        self._act_pause.toggled.connect(self._on_pause_toggled)
        menu.addAction(self._act_pause)

        menu.addSeparator()

        act_settings = QAction("Settings…", self)
        act_settings.triggered.connect(self._on_open_settings)
        menu.addAction(act_settings)

        menu.addSeparator()

        act_about = QAction(f"About {__app_name__}", self)
        act_about.triggered.connect(self._on_about)
        menu.addAction(act_about)

        act_quit = QAction("Quit", self)
        act_quit.triggered.connect(self._quit)
        menu.addAction(act_quit)

        self._tray.setContextMenu(menu)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        # Single-click on the tray icon opens History
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._on_open_history()

    # -- slots ------------------------------------------------------------

    def _on_capture_screen(self) -> None:
        """Start an interactive screen-region capture (Windows Snipping Tool style)."""
        save_dir = resolve_image_save_dir(self._config.capture.image_save_dir)
        capture_screen_region(
            parent=self._window,
            save_dir=save_dir,
            copy_to_clipboard=self._config.capture.image_copy_to_clipboard,
            finished_callback=self._on_screen_capture_finished,
        )

    def _on_screen_capture_finished(self, path: Path | None) -> None:
        """Store the saved screenshot in the DB and refresh UI/feedback."""
        if path is None:
            return
        snip = self._db.add_image_snippet(path)
        if snip is None:
            return
        self._refresh_tray_tooltip(last_content=f"image: {path.name}")
        if self._config.feedback.toast or self._config.feedback.tray_flash:
            self._feedback.show_capture(f"Screenshot saved: {path.name}", "image")
        if self._config.feedback.sound:
            self._feedback.play_capture_sound()
        LOGGER.info("Captured screenshot #%d -> %s", snip.id, path)

    def _on_capture(self, content: str) -> None:
        det = detect(content)
        snip = self._db.add_snippet(
            content,
            det.type,
            is_sensitive=(det.type == "text" and det.confidence == 1.0 and False),  # Phase 5
        )
        if snip is None:
            return
        # Refresh the tray tooltip with the new count + last capture
        self._refresh_tray_tooltip(last_content=content)
        # Feedback (toast + tray flash)
        if self._config.feedback.toast or self._config.feedback.tray_flash:
            self._feedback.show_capture(content, det.type)
        # WS9: optional capture sound
        if self._config.feedback.sound:
            self._feedback.play_capture_sound()
        LOGGER.info(
            "Captured #%d (%s, %d chars, conf=%.2f)",
            snip.id, det.type, len(content), det.confidence,
        )

    def _on_snippet_activated(self, snip: Snippet) -> None:
        # Bump usage stats every time a snippet is intentionally recalled.
        self._db.bump_used(snip.id)
        if snip.content_type == "image":
            self._activate_image_snippet(snip)
            return
        # Mark this as a self-copy so the watcher won't re-store it
        self._clipboard.mark_self_copy(snip.content)
        self._qt.clipboard().setText(snip.content)
        # Refresh the row counters
        self._refresh_tray_tooltip(last_content=snip.content)
        # Brief confirmation toast
        if self._config.feedback.toast:
            self._feedback.show_capture(snip.content, snip.content_type)
        # Refresh open views so the new use_count / last_used_at are visible.
        if self._history_window is not None:
            self._history_window._refresh()

    def _activate_image_snippet(self, snip: Snippet) -> None:
        """Copy an image snippet's file to the clipboard as a pixmap."""
        from PySide6.QtGui import QPixmap

        path = Path(snip.content)
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            LOGGER.warning("Could not load image for snippet #%d: %s", snip.id, path)
            return
        copy_pixmap_to_clipboard(pixmap)
        self._refresh_tray_tooltip(last_content=f"image: {path.name}")
        if self._config.feedback.toast:
            self._feedback.show_capture(f"Copied image: {path.name}", "image")
        LOGGER.info("Copied image #%d to clipboard: %s", snip.id, path)
        if self._history_window is not None:
            self._history_window._refresh()

    def _on_pause_toggled(self, paused: bool) -> None:
        if paused:
            self._clipboard.stop()
            self._tray.setToolTip(f"{__app_name__} â€” capture paused")
            LOGGER.info("Capture paused by user")
        else:
            self._clipboard.start()
            self._refresh_tray_tooltip()
            LOGGER.info("Capture resumed by user")

    def _on_about(self) -> None:
        """Show a custom About dialog that uses the app's theme (v0.3.0+).

        `QMessageBox.about()` inherits the OS theme, so the text often
        appears in a low-contrast default that ignores the user's
        midnight/solarized/paper-light selection. A plain QDialog with
        explicit stylesheet rules gives us readable text in every theme.
        """
        dlg = QDialog(self._window)
        dlg.setObjectName("aboutDialog")
        dlg.setWindowTitle(f"About {__app_name__}")
        dlg.setMinimumSize(420, 280)
        dlg.setWindowIcon(_build_default_icon())

        root = QVBoxLayout(dlg)
        root.setContentsMargins(24, 20, 24, 16)
        root.setSpacing(12)

        # Header: app name + version
        title = QLabel(f"{__app_name__} {__version__}", dlg)
        title_font = QFont()
        title_font.setPointSize(18)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setObjectName("aboutTitle")
        root.addWidget(title)

        # Subtitle
        sub = QLabel("Smart clipboard & snippet manager.", dlg)
        sub.setObjectName("aboutSubtitle")
        sub.setWordWrap(True)
        root.addWidget(sub)

        # Body (use a QFrame so we can give it a subtle card background)
        card = QFrame(dlg)
        card.setObjectName("aboutCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(6)

        history_open_phrase = (
            "when you click the tray icon or choose Open Snippy from the menu"
            if sys.platform == "darwin"
            else f"when you press {self._config.hotkey}"
        )
        body = QLabel(
            "Snippy watches your clipboard, stores snippets in a local "
            "SQLite database, and opens the History window "
            f"({history_open_phrase}) when you need to recall something you copied.\n\n"
            "\u2022 100% local  \u2014  no telemetry, no cloud, no account\n"
            "\u2022 MIT licensed\n"
            "\u2022 End-to-end AES-256 encrypted `.snip` bundles for "
            "cross-device sync",
            dlg,
        )
        body.setObjectName("aboutBody")
        body.setWordWrap(True)
        body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        card_layout.addWidget(body)
        root.addWidget(card)

        # Links row: repo + help
        links_row = QHBoxLayout()
        links_row.setSpacing(12)

        repo_label = QLabel(
            f"<a href='https://github.com/tbertha35/snippy'>github.com/tbertha35/snippy</a>",
            dlg,
        )
        repo_label.setObjectName("aboutLink")
        repo_label.setOpenExternalLinks(True)
        repo_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
            | Qt.TextInteractionFlag.TextSelectableByMouse
        )
        links_row.addWidget(repo_label)
        links_row.addStretch(1)

        root.addLayout(links_row)

        # Close button (right-aligned)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dlg)
        buttons.rejected.connect(dlg.reject)
        buttons.accepted.connect(dlg.accept)
        # QDialogButtonBox's Close role is a "reject" button, but we
        # want the same behavior as OK for the close: rewire so
        # both accept and reject just close the dialog.
        close_btn = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_btn is not None:
            close_btn.setText("Close")
            close_btn.clicked.connect(dlg.accept)
        root.addWidget(buttons)

        dlg.exec()
        dlg.deleteLater()

    def _on_open_settings(self) -> None:
        """Open (or raise) the Settings dialog. WS1."""
        from snippy.ui.settings import SettingsDialog

        if self._settings_dialog is None or not self._settings_dialog.isVisible():
            self._settings_dialog = SettingsDialog(
                self._config, parent=None,
                db_provider=lambda: self._db,
            )
            self._settings_dialog.config_changed.connect(self._on_config_changed)
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    def _on_export_backup(self) -> None:
        """Phase 3a: open the Backup dialog and let the user export."""
        from snippy.ui.backup import BackupDialog
        dlg = BackupDialog(self._window, self._db)
        dlg.export_completed.connect(self._on_backup_completed)
        dlg.exec()
        dlg.deleteLater()

    def _on_backup_completed(self, path: str, snippet_count: int) -> None:
        """Stamp the backup timestamp and refresh the in-memory reminder."""
        from snippy.core.scheduler import mark_just_backed_up
        new_cfg = mark_just_backed_up(self._config, self._db)
        self._config = new_cfg
        if getattr(self, "_reminder", None):
            self._reminder = type(self._reminder)(self._config)
        LOGGER.info("Backup completed: %s (%d snippets); reminder reset", path, snippet_count)

    def _on_import_backup(self) -> None:
        """Phase 3a: open the Backup dialog and let the user import."""
        from snippy.ui.backup import BackupDialog
        dlg = BackupDialog(self._window, self._db)
        if dlg.exec():
            # Refresh history in case the import added new snippets
            if self._history_window is not None:
                self._history_window._refresh()
            self._refresh_tray_tooltip()
        dlg.deleteLater()

    def _on_bundle_dropped(self, path: str) -> None:
        """Phase 3a / v0.3.0: import a `.snip` file dropped on the History window.

        Reuses the same flow as the Backup dialog: peek the manifest to
        detect encryption, prompt for a passphrase if needed, then
        `import_bundle`. Posts a tray notification with the result.
        """
        from snippy.core.bundle import import_bundle
        from snippy.ui.backup import peek_manifest
        # Detect encryption
        try:
            manifest = peek_manifest(path)
        except Exception as exc:
            self._tray.showMessage(
                f"{__app_name__} — import failed",
                f"Could not read {Path(path).name}: {exc}",
                QSystemTrayIcon.MessageIcon.Critical,
                7000,
            )
            LOGGER.warning("Bundle drop: read manifest failed: %s", exc)
            return
        passphrase: str | None = None
        if manifest.encrypted:
            # Defer to the Backup dialog: it already has the right UI.
            self._on_import_backup()
            return
        # Plain text bundle \u2014 import directly
        try:
            result = import_bundle(path, self._db, mode="merge")
        except Exception as exc:
            self._tray.showMessage(
                f"{__app_name__} — import failed",
                str(exc),
                QSystemTrayIcon.MessageIcon.Critical,
                7000,
            )
            LOGGER.warning("Bundle drop: import failed: %s", exc)
            return
        # Refresh downstream consumers
        if self._history_window is not None:
            self._history_window._refresh()
        self._refresh_tray_tooltip()
        msg = f"Imported {result.imported}, skipped {result.skipped}, updated {result.updated}"
        if result.errors:
            msg += f", {len(result.errors)} error(s)"
        self._tray.showMessage(
            f"{__app_name__} — drop import",
            f"{Path(path).name}: {msg}",
            QSystemTrayIcon.MessageIcon.Information if not result.errors else QSystemTrayIcon.MessageIcon.Warning,
            5000,
        )
        LOGGER.info("Bundle drop: %s -> %s", path, msg)

    def _on_backup_check(self) -> None:
        """Phase 3a: if the user hasn't backed up in `reminder_days`, show a toast."""
        if not getattr(self, "_reminder", None):
            return
        msg = self._reminder.message()
        if not msg:
            return
        # Non-modal reminder; we use a real tray notification so the
        # message survives the user closing the toast quickly.
        self._tray.showMessage(
            f"{__app_name__} — backup reminder",
            msg,
            QSystemTrayIcon.MessageIcon.Warning,
            7000,
        )
        LOGGER.info("Backup reminder shown: %r", msg)

    def _on_open_history(self) -> None:
        """Open (or raise) the History window. WS2."""
        from snippy.ui.history import HistoryWindow

        if self._history_window is None:
            self._history_window = HistoryWindow(parent=None)
            self._history_window.set_snippet_provider(self._db.list_all_for_search)
            self._history_window.set_tags_provider(self._db.tags_by_snippet)  # WS6
            self._history_window.set_tag_lister(lambda: [t["name"] for t in self._db.list_tags()])  # WS6
            self._history_window.snippet_activated.connect(self._on_snippet_activated)
            self._history_window.snippets_deleted.connect(self._on_bulk_delete)
            self._history_window.snippets_archived.connect(self._on_bulk_archive)
            self._history_window.snippets_unarchived.connect(self._on_bulk_unarchive)  # v0.3.x
            self._history_window.snippets_pinned.connect(self._on_bulk_pin)
            self._history_window.snippets_reordered.connect(self._on_bulk_reordered)
            self._history_window.snippet_details_requested.connect(self._open_detail_for)  # WS4
            self._history_window.tags_requested.connect(self._on_tags_add)        # WS6
            self._history_window.tags_remove_requested.connect(self._on_tags_remove)  # WS6
            # v0.3.x: provider that returns the archived rows for the
            # Archived view toggle.
            self._history_window.set_archived_provider(self._db.list_archived)
        self._history_window.show_and_focus()

    # -- WS5 bulk ops ---------------------------------------------------

    def _on_bulk_delete(self, snippets: list[Snippet]) -> None:
        for s in snippets:
            self._db.delete_snippet(s.id)
        self._refresh_tray_tooltip()
        if self._history_window is not None:
            self._history_window._refresh()
        LOGGER.info("Bulk-deleted %d snippet(s)", len(snippets))

    def _on_bulk_archive(self, snippets: list[Snippet]) -> None:
        for s in snippets:
            self._db.set_archived(s.id, True)
        self._refresh_tray_tooltip()
        if self._history_window is not None:
            self._history_window._refresh()
        LOGGER.info("Bulk-archived %d snippet(s)", len(snippets))

    def _on_bulk_unarchive(self, snippets: list[Snippet]) -> None:
        for s in snippets:
            self._db.set_archived(s.id, False)
        self._refresh_tray_tooltip()
        if self._history_window is not None:
            self._history_window._refresh()
        LOGGER.info("Bulk-unarchived %d snippet(s)", len(snippets))

    def _on_bulk_pin(self, snippets: list[Snippet], pinned: bool) -> None:
        for s in snippets:
            self._db.set_pinned(s.id, pinned)
        if self._history_window is not None:
            self._history_window._refresh()
        LOGGER.info("Bulk-%s %d snippet(s)", "pinned" if pinned else "unpinned", len(snippets))

    def _on_bulk_reordered(self, snippets: list[Snippet]) -> None:
        """Persist a new manual pin order from drag-and-drop."""
        for order, s in enumerate(snippets, start=1):
            self._db.set_pin_order(s.id, order)
        LOGGER.info("Reordered %d pinned snippet(s)", len(snippets))

    # -- WS6 tag handlers -----------------------------------------------

    def _on_tags_add(self, tag_names: list[str]) -> None:
        """Add the given tags to the currently-selected snippets (union semantics)."""
        if not tag_names or self._history_window is None:
            return
        snips = self._history_window._selected_snippets()
        if not snips:
            return
        # Normalize + ensure tags exist
        norm = sorted({t.strip().lower() for t in tag_names if t.strip()})
        for s in snips:
            existing = set(self._db.get_tags_for_snippet(s.id))
            self._db.set_tags_for_snippet(s.id, list(existing | set(norm)))
        self._history_window._refresh()
        LOGGER.info("Added tags %s to %d snippet(s)", norm, len(snips))

    def _on_tags_remove(self, snippets: list[Snippet], tag_name: str) -> None:
        norm = tag_name.strip().lower()
        for s in snippets:
            existing = set(self._db.get_tags_for_snippet(s.id))
            if norm in existing:
                self._db.set_tags_for_snippet(s.id, list(existing - {norm}))
        if self._history_window is not None:
            self._history_window._refresh()
        LOGGER.info("Removed tag %r from %d snippet(s)", norm, len(snippets))

    # -- WS4: snippet detail modal ---------------------------------------

    def _open_detail_for(self, snippet: Snippet) -> None:
        """Open the detail dialog for the given snippet (WS4)."""
        from snippy.ui.detail import SnippetDetailDialog
        # Re-fetch from DB so we always have the latest copy
        fresh = self._db.get_by_id(snippet.id) or snippet
        dlg = SnippetDetailDialog(fresh, parent=self._window)
        dlg.snippet_deleted.connect(self._on_detail_deleted)
        dlg.snippet_saved.connect(self._on_detail_saved)
        self._detail_dialog = dlg  # hold a reference so it doesn't GC
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_detail_deleted(self, snippet_id: int) -> None:
        self._db.delete_snippet(snippet_id)
        self._refresh_tray_tooltip()
        if self._history_window is not None:
            self._history_window._refresh()
        LOGGER.info("Snippet #%d deleted from detail dialog", snippet_id)

    def _on_detail_saved(self, snippet: Snippet) -> None:
        updated = self._db.update_snippet_content(snippet.id, snippet.content)
        if updated is not None:
            # Re-detect type since content may have changed (url -> text, etc.)
            from snippy.core.detector import detect
            det = detect(updated.content)
            if det.type != updated.content_type:
                self._db._conn.execute(
                    "UPDATE snippets SET content_type = ? WHERE id = ?",
                    (det.type, updated.id),
                )
                self._db._conn.commit()
        self._refresh_tray_tooltip()
        if self._history_window is not None:
            self._history_window._refresh()
        LOGGER.info("Snippet #%d updated from detail dialog", snippet.id)

    def _on_config_changed(self, new_config: SnippyConfig) -> None:
        """Apply a freshly-saved config to the live app."""
        old_hotkey = self._config.hotkey
        old_image_hotkey = self._config.capture.image_hotkey
        self._config = new_config
        # Re-apply the theme immediately
        from snippy.ui.styles import stylesheet_for
        self._qt.setStyleSheet(stylesheet_for(new_config.theme))
        # Honor capture-enabled toggle without restart
        if new_config.capture.enabled:
            self._clipboard.start()
        else:
            self._clipboard.stop()
        # Rebind the global hotkeys if they changed (not supported on macOS).
        if (
            sys.platform != "darwin"
            and (
                new_config.hotkey != old_hotkey
                or new_config.capture.image_hotkey != old_image_hotkey
            )
        ):
            self._register_hotkeys(new_config.hotkey, new_config.capture.image_hotkey)
        self._refresh_tray_tooltip()
        LOGGER.info("Config applied live: theme=%s, sound=%s, capture=%s",
                    new_config.theme, new_config.feedback.sound, new_config.capture.enabled)

    def _on_snippets_changed(self) -> None:
        """Refresh any open views so captures/mutations appear live."""
        if self._history_window is not None and self._history_window.isVisible():
            self._history_window._refresh()

    def _refresh_tray_tooltip(self, last_content: str | None = None) -> None:
        """WS9: smarter tray tooltip —” '142 active Â· last: github.com/... (3m ago)'."""
        from datetime import datetime, timezone
        stats = self._db.stats()
        if self._clipboard and getattr(self._clipboard, "_is_running", lambda: True)() is False:
            self._tray.setToolTip(f"{__app_name__} —” capture paused")
            return
        if last_content:
            preview = last_content.splitlines()[0] if last_content else ""
            if len(preview) > 40:
                preview = preview[:37] + "…"
            now = datetime.now(timezone.utc)
            time_str = now.strftime("%H:%M")
            self._tray.setToolTip(
                f"{__app_name__} \u2014 {stats['active']} active \u00b7 last: {preview} ({time_str})"
            )
        else:
            self._tray.setToolTip(f"{__app_name__} \u2014 {stats['active']} active snippet(s)")

    def _register_hotkeys(self, history_hotkey: str, image_hotkey: str) -> bool:
        """Register system-wide hotkeys, falling back to in-app shortcuts.

        Returns True if both global hotkeys registered successfully.
        """
        from PySide6.QtGui import QShortcut

        # Clear any previous fallback shortcuts.
        for sc in self._fallback_shortcuts:
            sc.setEnabled(False)
        self._fallback_shortcuts.clear()

        self._global_hotkey.unregister()
        self._image_hotkey.unregister()

        ok_history = self._global_hotkey.register(history_hotkey, self._on_open_history)
        if not ok_history:
            LOGGER.warning(
                "System-wide history hotkey %r unavailable; using in-app shortcut",
                history_hotkey,
            )
            sc = QShortcut(QKeySequence(history_hotkey), self._window)
            sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
            sc.activated.connect(self._on_open_history)
            self._fallback_shortcuts.append(sc)

        ok_image = self._image_hotkey.register(image_hotkey, self._on_capture_screen)
        if not ok_image:
            LOGGER.warning(
                "System-wide image hotkey %r unavailable; using in-app shortcut",
                image_hotkey,
            )
            sc = QShortcut(QKeySequence(image_hotkey), self._window)
            sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
            sc.activated.connect(self._on_capture_screen)
            self._fallback_shortcuts.append(sc)

        return ok_history and ok_image

    def _quit(self) -> None:
        LOGGER.info("Quit requested")
        try:
            self._global_hotkey.unregister()
            self._image_hotkey.unregister()
            for sc in self._fallback_shortcuts:
                sc.setEnabled(False)
            self._clipboard.stop()
            self._db.close()
        finally:
            self._qt.quit()

    # -- startup ---------------------------------------------------------

    def start(self) -> None:
        self._tray.show()
        if self._config.capture.enabled:
            self._clipboard.start()
        else:
            self._act_pause.setChecked(True)
        stats = self._db.stats()
        LOGGER.info(
            "Snippy started — %d active snippet(s), %d total",
            stats["active"], stats["total"],
        )
        if sys.platform == "darwin":
            # macOS does not use global hotkeys; keep the welcome message short.
            self._tray.showMessage(
                __app_name__,
                f"Running. {stats['active']} snippet(s) stored. "
                "Click the tray icon or use the menu to open Snippy.",
                QSystemTrayIcon.MessageIcon.Information,
                3000,
            )
        elif not self._hotkey_ok:
            self._tray.showMessage(
                __app_name__,
                "Global hotkey disabled. Grant Accessibility to Snippy in "
                "System Settings → Privacy & Security → Accessibility, then restart.",
                QSystemTrayIcon.MessageIcon.Warning,
                7000,
            )
        else:
            self._tray.showMessage(
                __app_name__,
                f"Running. {stats['active']} snippet(s) stored. {self._config.hotkey} to open.",
                QSystemTrayIcon.MessageIcon.Information,
                2500,
            )

        # Phase 3a: backup reminder scheduler. The first check happens
        # immediately; if the user is stale, we show a non-modal toast.
        # The 6-hour repeating check covers the "computer is on, user
        # never closes the app" case (otherwise the start-time check
        # would be the only one).
        from PySide6.QtCore import QTimer
        from snippy.core.scheduler import BackupReminder
        self._reminder = BackupReminder(self._config)
        self._backup_timer = QTimer(self)
        self._backup_timer.setInterval(6 * 60 * 60 * 1000)  # 6h
        self._backup_timer.timeout.connect(self._on_backup_check)
        self._backup_timer.start()
        QTimer.singleShot(2000, self._on_backup_check)  # small delay so the welcome toast shows first


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run(argv: Sequence[str] | None = None) -> int:
    # Pin the Windows AppUserModelID *before* QApplication is created so
    # the taskbar / Alt-Tab / task-manager icon all line up with the
    # running .exe. (No-op on macOS/Linux.)
    _set_windows_app_user_model_id()

    args = _parse_args(argv if argv is not None else sys.argv[1:])
    setup_logging(debug=args.debug)
    LOGGER.info(
        "Starting %s v%s (debug=%s, autostart=%s)",
        __app_name__, __version__, args.debug, args.autostart,
    )

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(__app_name__)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(__app_name__)
    # Apply the real app icon (so the taskbar / dock / window titlebar
    # all show the Snippy logo, not the generic Python one). The
    # `QIcon(pixmap)` call loads the bundled PNG; if the PNG is missing
    # (e.g. in a CI smoke test), Qt falls back to a default icon.
    from snippy.ui.feedback import _build_default_icon
    app_icon = _build_default_icon()
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)
    # Phase 1: Snippy lives in the tray. Don't quit just because the
    # status window is closed.
    app.setQuitOnLastWindowClosed(False)

    # Apply theme globally
    config = load_config()
    app.setStyleSheet(stylesheet_for(config.theme))

    # Some Qt platforms need this to allow the tray icon to actually show.
    # On macOS specifically, the app must not be a "regular" .app bundle
    # for the tray to work; user can re-check this in Phase 5.
    if not QSystemTrayIcon.isSystemTrayAvailable():
        LOGGER.warning("System tray is not available on this platform. Continuing anyway.")

    snippy = SnippyApp(app, config)
    snippy.start()

    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run())
