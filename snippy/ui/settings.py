"""Settings dialog for Snippy \u2014 Phase 2 WS1.

A modal `QDialog` with a left-rail nav (`QListWidget`) and a stacked
`QStackedWidget` of page widgets on the right. When the user clicks
**OK** (or **Apply**), the dialog writes the in-memory config back
through `core.config.save_config`.

Pages:
    - **General**     \u2014 launch-at-login toggle, default theme
    - **Hotkeys**     \u2014 Snippy hotkey editor
    - **Capture**     \u2014 capture-enabled checkbox, blacklist regex editor, sound toggle
    - **Theme**       \u2014 radio list of bundled themes + live preview pane
    - **Backup**      \u2014 "Coming in v0.3.0" placeholder

The dialog is purely UI \u2014 it does *not* try to re-apply settings on the
fly beyond emitting `config_changed(SnippyConfig)`. The app subscribes
to that signal in `app.py` (Phase 2 wiring).
"""
from __future__ import annotations

import logging
import sys
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from snippy import __app_name__, __version__
from snippy.core import autostart
from snippy.core.config import SnippyConfig, save_config


LOGGER = logging.getLogger(__name__)


# Page identifiers for the stacked widget
_PAGE_GENERAL = 0
_PAGE_HOTKEYS = 1
_PAGE_CAPTURE = 2
_PAGE_THEME = 3
_PAGE_BACKUP = 4


class SettingsDialog(QDialog):
    """Left-rail settings dialog. Emits `config_changed` on Apply/OK."""

    config_changed = Signal(SnippyConfig)

    def __init__(
        self,
        config: SnippyConfig,
        parent: QWidget | None = None,
        *,
        db_provider: Callable[[], "object | None"] | None = None,
    ) -> None:
        """Build the Settings dialog.

        `db_provider` is an optional zero-arg callable that returns the
        live `core.db.Database` (or anything that quacks like one — we
        only call `.count()` on it). It's used by the Backup tab to
        show "Snippets stored: N" and by the "Open Backup dialog\u2026"
        button to open a `BackupDialog` against the live db. If not
        provided, the Backup tab is read-only (the spinner + open button
        still work, the snippet-count line shows "\u2014").
        """
        super().__init__(parent)
        self._original = config
        self._working = _copy_config(config)  # mutated as the user edits
        self._db_provider = db_provider

        self.setObjectName("settings")
        self.setWindowTitle(f"{__app_name__} \u2014 Settings")
        self.setMinimumSize(720, 480)
        self.resize(820, 540)

        self._build_ui()
        self._populate_from_config()

    # -- public API -------------------------------------------------------

    def current_config(self) -> SnippyConfig:
        # Collect from widgets each time so the returned config always
        # reflects the current UI state (used by the test suite and by any
        # consumer that wants the live config without going through Apply).
        self._collect_into_working()
        return _copy_config(self._working)

    # -- UI build --------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Left rail
        self._nav = QListWidget(self)
        self._nav.setObjectName("settings_nav")
        self._nav.setFrameShape(QFrame.Shape.NoFrame)
        self._nav.setUniformItemSizes(True)
        self._nav.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._nav.setFixedWidth(180)
        # macOS does not support global hotkeys for this app, so hide the
        # Hotkeys page entirely.
        nav_items = [
            ("General",  _PAGE_GENERAL, "\u2699"),
            ("Capture",  _PAGE_CAPTURE, "\u2702"),
            ("Theme",    _PAGE_THEME,   "\u00a7"),
            ("Backup",   _PAGE_BACKUP,  "\u21bb"),
        ]
        if sys.platform != "darwin":
            nav_items.insert(1, ("Hotkeys", _PAGE_HOTKEYS, "\u2328"))
        for label, page_id, icon in nav_items:
            item = QListWidgetItem(f"{icon}  {label}")
            self._nav.addItem(item)
            self._nav.item(self._nav.count() - 1).setData(Qt.ItemDataRole.UserRole, page_id)
        outer.addWidget(self._nav)

        # Vertical separator
        sep = QFrame(self)
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        outer.addWidget(sep)

        # Stacked pages (build BEFORE wiring the nav signal so the callback
        # has a valid target).
        self._pages = QStackedWidget(self)
        self._pages.addWidget(self._build_general_page())
        if sys.platform != "darwin":
            self._pages.addWidget(self._build_hotkeys_page())
        self._pages.addWidget(self._build_capture_page())
        self._pages.addWidget(self._build_theme_page())
        self._pages.addWidget(self._build_backup_page())
        outer.addWidget(self._pages, stretch=1)

        # Now safe to wire the nav.
        self._nav.setCurrentRow(_PAGE_GENERAL)
        self._nav.currentRowChanged.connect(self._pages.setCurrentIndex)

        # Button row
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Apply
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self._on_apply)
        outer.addWidget(buttons)

    # -- Page builders ---------------------------------------------------

    def _build_general_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        title = QLabel("General", page)
        title_font = QFont(); title_font.setPointSize(16); title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        group = QGroupBox("Startup", page)
        form = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._chk_autostart = QCheckBox("Launch Snippy at login", group)
        self._chk_autostart.setToolTip(
            "Adds Snippy to your OS's startup mechanism (Windows registry, "
            "macOS LaunchAgent, or Linux XDG autostart)."
        )
        form.addRow(self._chk_autostart)

        info = QLabel("Snippy keeps a status window on the taskbar so you can\n"
                      "alt-tab to it and pin it. Closing it won't quit Snippy.", group)
        info.setStyleSheet("color: gray;")
        form.addRow(info)

        layout.addWidget(group)
        layout.addStretch(1)
        return page

    def _build_hotkeys_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        title = QLabel("Hotkeys", page)
        title_font = QFont(); title_font.setPointSize(16); title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        group = QGroupBox("Snippy", page)
        form = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._edt_hotkey = QLineEdit(group)
        self._edt_hotkey.setPlaceholderText("e.g. Ctrl+Space, Alt+P, Ctrl+Shift+V")
        self._edt_hotkey.setToolTip(
            "Enter the hotkey sequence that opens the Snippy History window.\n"
            "Use the form: Ctrl+Key, Alt+Key, Shift+Key, or any combination\n"
            "joined with '+' (e.g. Ctrl+Shift+V)."
        )
        form.addRow("Open Snippy:", self._edt_hotkey)

        self._edt_image_hotkey = QLineEdit(group)
        self._edt_image_hotkey.setPlaceholderText("e.g. Ctrl+Shift+Space")
        self._edt_image_hotkey.setToolTip(
            "Global hotkey that starts an interactive screen-region capture.\n"
            "Same format as the Snippy hotkey."
        )
        form.addRow("Capture screen region:", self._edt_image_hotkey)

        hint = QLabel(
            "Changes take effect immediately on Apply/OK.",
            group,
        )
        hint.setStyleSheet("color: gray;")
        form.addRow(hint)

        layout.addWidget(group)
        layout.addStretch(1)
        return page

    def _build_capture_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        title = QLabel("Capture", page)
        title_font = QFont(); title_font.setPointSize(16); title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        # Capture group
        cap = QGroupBox("What to capture", page)
        cap_form = QFormLayout(cap)
        cap_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._chk_capture_enabled = QCheckBox("Capture clipboard automatically", cap)
        cap_form.addRow(self._chk_capture_enabled)

        self._spn_max_length = QSpinBox(cap)
        self._spn_max_length.setRange(100, 1_000_000)
        self._spn_max_length.setSuffix(" chars")
        cap_form.addRow("Max snippet length:", self._spn_max_length)

        self._spn_max_snippets = QSpinBox(cap)
        self._spn_max_snippets.setRange(100, 1_000_000)
        self._spn_max_snippets.setSuffix(" snippets")
        cap_form.addRow("Library cap:", self._spn_max_snippets)

        self._chk_image_copy = QCheckBox("Copy captured screenshots to clipboard", cap)
        self._chk_image_copy.setToolTip(
            "When checked, the captured image is placed on the clipboard\n"
            "immediately after saving, so you can paste it elsewhere."
        )
        cap_form.addRow(self._chk_image_copy)

        # Image save directory
        dir_row = QHBoxLayout()
        self._lbl_image_dir = QLabel("", cap)
        self._lbl_image_dir.setToolTip("Screenshots are saved here.")
        self._lbl_image_dir.setWordWrap(True)
        self._btn_image_dir = QPushButton("Browse…", cap)
        self._btn_image_dir.clicked.connect(self._on_browse_image_dir)
        dir_row.addWidget(self._lbl_image_dir, stretch=1)
        dir_row.addWidget(self._btn_image_dir)
        cap_form.addRow("Screenshot folder:", dir_row)

        layout.addWidget(cap)

        # Blacklist group
        bl = QGroupBox("Blacklist (regex, one per line)", page)
        bl_layout = QVBoxLayout(bl)
        self._edt_blacklist = QPlainTextEdit(bl)
        self._edt_blacklist.setPlaceholderText(
            r"(?i)password\s*[:=]\s*\S+  \u2014 ignore 'password: foo' style copies"
        )
        bl_layout.addWidget(self._edt_blacklist)
        layout.addWidget(bl)

        # Feedback group
        fb = QGroupBox("Feedback when capturing", page)
        fb_layout = QVBoxLayout(fb)

        self._chk_toast = QCheckBox("Toast (1.5s corner notification)", fb)
        fb_layout.addWidget(self._chk_toast)

        self._chk_tray_flash = QCheckBox("Tray icon flash (color-coded by type)", fb)
        fb_layout.addWidget(self._chk_tray_flash)


        self._chk_sound = QCheckBox("Capture sound (subtle ding, off by default)", fb)
        fb_layout.addWidget(self._chk_sound)

        self._chk_sensitive = QCheckBox("Warn before storing likely passwords/keys", fb)
        fb_layout.addWidget(self._chk_sensitive)

        layout.addWidget(fb)
        layout.addStretch(1)
        return page

    def _build_theme_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        title = QLabel("Theme", page)
        title_font = QFont(); title_font.setPointSize(16); title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        # Theme radio buttons
        theme_group = QGroupBox("Bundled themes", page)
        tg_layout = QVBoxLayout(theme_group)
        self._rad_midnight = QRadioButton("Midnight (default \u2014 dark blue)", theme_group)
        self._rad_solarized = QRadioButton("Solarized Dark", theme_group)
        tg_layout.addWidget(self._rad_midnight)
        tg_layout.addWidget(self._rad_solarized)
        layout.addWidget(theme_group)

        # Preview pane
        preview = QGroupBox("Preview", page)
        pv_layout = QVBoxLayout(preview)
        self._preview_label = QLabel(
            "\u2728 This is what a snippet will look like in the History window.\n\n"
            "Type to search, \u2191/\u2193 to navigate, \u21b5 to copy.",
            preview,
        )
        self._preview_label.setStyleSheet(
            "background: palette(base); padding: 12px; border-radius: 6px;"
            "font-family: 'Segoe UI', sans-serif;"
        )
        self._preview_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        pv_layout.addWidget(self._preview_label)
        layout.addWidget(preview, stretch=1)
        return page

    def _build_backup_page(self) -> QWidget:
        """Phase 3a / v0.3.0 \u2014 the real Backup & Restore tab.

        Two pieces:
            \u2022 'Open Backup dialog\u2026' button \u2192 opens the BackupDialog
              (lives in `ui/backup.py`). The dialog itself handles the
              file pickers and passphrase prompts; this page is the
              'Settings entry point' to it.
            \u2022 'Reminder days' spinbox (1-90) \u2192 controls how stale a
              backup can get before the tray posts a reminder toast.
        """
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        title = QLabel("Backup & Restore", page)
        title_font = QFont(); title_font.setPointSize(16); title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        # \u2014\u2014 Status \u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014
        status_group = QGroupBox("Status", page)
        status_form = QFormLayout(status_group)
        status_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._lbl_last_backup = QLabel("\u2014", status_group)
        self._lbl_last_backup.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        status_form.addRow("Last backup:", self._lbl_last_backup)

        self._lbl_snippet_count = QLabel("\u2014", status_group)
        status_form.addRow("Snippets stored:", self._lbl_snippet_count)

        layout.addWidget(status_group)

        # \u2014\u2014 Reminder \u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014
        reminder_group = QGroupBox("Auto-reminder", page)
        reminder_form = QFormLayout(reminder_group)
        reminder_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._spn_reminder_days = QSpinBox(reminder_group)
        self._spn_reminder_days.setRange(1, 90)
        self._spn_reminder_days.setSuffix(" day(s)")
        self._spn_reminder_days.setToolTip(
            "If your last backup is older than this, Snippy will show a\n"
            "non-modal reminder toast on app start and every 6 hours after."
        )
        reminder_form.addRow("Remind me after:", self._spn_reminder_days)

        reminder_hint = QLabel(
            "Default: 7 days. The reminder is a toast only \u2014 Snippy does\n"
            "NOT auto-export; you always choose when and where.",
            reminder_group,
        )
        reminder_hint.setStyleSheet("color: gray;")
        reminder_hint.setWordWrap(True)
        reminder_form.addRow(reminder_hint)

        layout.addWidget(reminder_group)

        # \u2014\u2014 Actions \u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014
        actions_group = QGroupBox("Manual backup", page)
        actions_layout = QVBoxLayout(actions_group)

        open_btn = QPushButton(
            "\U0001f4e4\u00a0Open Backup dialog\u2026",
            actions_group,
        )
        open_btn.setMinimumHeight(36)
        open_btn.setToolTip(
            "Opens the Backup & Restore dialog (Export / Import / Schedule)."
        )
        open_btn.clicked.connect(self._on_open_backup_dialog)
        actions_layout.addWidget(open_btn)

        self._lbl_dialog_status = QLabel("", actions_group)
        self._lbl_dialog_status.setStyleSheet("color: gray;")
        self._lbl_dialog_status.setWordWrap(True)
        actions_layout.addWidget(self._lbl_dialog_status)

        layout.addWidget(actions_group)
        layout.addStretch(1)
        return page

    # -- Populate from config -------------------------------------------

    def _populate_from_config(self) -> None:
        c = self._working
        # General
        self._chk_autostart.setChecked(autostart.is_enabled())
        # Hotkeys (not present on macOS)
        if sys.platform != "darwin":
            self._edt_hotkey.setText(c.hotkey)
            self._edt_image_hotkey.setText(c.capture.image_hotkey)
        # Capture
        self._chk_capture_enabled.setChecked(c.capture.enabled)
        self._spn_max_length.setValue(c.capture.max_snippet_length)
        self._spn_max_snippets.setValue(c.capture.max_snippets)
        self._chk_image_copy.setChecked(c.capture.image_copy_to_clipboard)
        self._update_image_dir_label(c.capture.image_save_dir)
        self._edt_blacklist.setPlainText("\n".join(c.capture.blacklist_regex))
        # Feedback
        self._chk_toast.setChecked(c.feedback.toast)
        self._chk_tray_flash.setChecked(c.feedback.tray_flash)
        self._chk_sound.setChecked(c.feedback.sound)
        self._chk_sensitive.setChecked(c.feedback.sensitive_warning)
        # Theme
        theme_radios = {
            "midnight": self._rad_midnight,
            "solarized-dark": self._rad_solarized,
        }
        theme_radios.get(c.theme, self._rad_midnight).setChecked(True)
        # Backup
        self._populate_backup_from_config()

    # -- Apply / accept --------------------------------------------------

    def _collect_into_working(self) -> None:
        c = self._working
        if sys.platform != "darwin":
            c.hotkey = self._edt_hotkey.text().strip() or "Ctrl+Space"
            c.capture.image_hotkey = self._edt_image_hotkey.text().strip() or "Ctrl+Shift+Space"
        c.capture.enabled = self._chk_capture_enabled.isChecked()
        c.capture.max_snippet_length = self._spn_max_length.value()
        c.capture.max_snippets = self._spn_max_snippets.value()
        c.capture.image_copy_to_clipboard = self._chk_image_copy.isChecked()
        c.capture.blacklist_regex = [
            line.strip() for line in self._edt_blacklist.toPlainText().splitlines() if line.strip()
        ]
        c.feedback.toast = self._chk_toast.isChecked()
        c.feedback.tray_flash = self._chk_tray_flash.isChecked()
        c.feedback.sound = self._chk_sound.isChecked()
        c.feedback.sensitive_warning = self._chk_sensitive.isChecked()
        if self._rad_solarized.isChecked():
            c.theme = "solarized-dark"
        else:
            c.theme = "midnight"
        # Autostart is system-state, not config; apply it here
        try:
            desired = self._chk_autostart.isChecked()
            if desired and not autostart.is_enabled():
                autostart.enable()
            elif not desired and autostart.is_enabled():
                autostart.disable()
        except Exception as exc:  # pragma: no cover (rare)
            LOGGER.warning("Failed to apply autostart toggle: %s", exc)
        # Backup
        c.backup.reminder_days = self._spn_reminder_days.value()
        # `last_backup_at` is read-only from this tab — it's stamped by
        # `core.scheduler.mark_just_backed_up` when the user finishes an
        # export through the Backup dialog.

    def _on_apply(self) -> None:
        self._collect_into_working()
        save_config(self._working)
        LOGGER.info("Settings applied")
        self.config_changed.emit(self.current_config())

    def _on_accept(self) -> None:
        self._on_apply()
        self.accept()

    # -- Backup tab helpers ----------------------------------------------

    def _populate_backup_from_config(self) -> None:
        """Fill the Backup tab from `self._working.backup` + the live db.

        Safe to call before any user interaction (this is the only path
        the dialog uses at startup). The status labels are read-only and
        refreshed each time we open the dialog, so a fresh export done in
        another window will be visible the next time the user opens
        Settings.
        """
        from snippy.core.scheduler import days_since_last

        b = self._working.backup
        # Reminder days
        self._spn_reminder_days.setValue(int(b.reminder_days))
        # Last backup timestamp (human-friendly)
        if b.last_backup_at is None:
            self._lbl_last_backup.setText("\u2014  (never \u2014 you should back up soon)")
        else:
            try:
                days = days_since_last(b)
                if days is None:
                    self._lbl_last_backup.setText(f"{b.last_backup_at} (malformed)")
                elif days == 0:
                    self._lbl_last_backup.setText(f"{b.last_backup_at}  (today)")
                elif days == 1:
                    self._lbl_last_backup.setText(f"{b.last_backup_at}  (yesterday)")
                else:
                    self._lbl_last_backup.setText(f"{b.last_backup_at}  ({days} days ago)")
            except Exception:
                self._lbl_last_backup.setText(b.last_backup_at)
        # Snippet count from the live db (if we have one)
        if self._db_provider is not None:
            try:
                db = self._db_provider()
                count = int(db.count()) if db is not None else 0
                self._lbl_snippet_count.setText(f"{count} snippet(s)")
            except Exception as exc:
                self._lbl_snippet_count.setText("\u2014  (db error)")
                LOGGER.warning("Failed to read snippet count: %s", exc)
        else:
            self._lbl_snippet_count.setText("\u2014")

    def _update_image_dir_label(self, configured: str | None) -> None:
        from snippy.core.capture_screen import default_image_save_dir

        path = configured or str(default_image_save_dir())
        # Truncate very long paths for the label.
        display = path if len(path) < 60 else f"...{path[-57:]}"
        self._lbl_image_dir.setText(display)
        self._lbl_image_dir.setToolTip(path)

    def _on_browse_image_dir(self) -> None:
        from snippy.core.capture_screen import default_image_save_dir, prompt_for_save_directory

        current = Path(self._working.capture.image_save_dir or default_image_save_dir())
        chosen = prompt_for_save_directory(self, current)
        if chosen is not None:
            self._working.capture.image_save_dir = str(chosen)
            self._update_image_dir_label(str(chosen))

    def _on_open_backup_dialog(self) -> None:
        """Open the BackupDialog against the live db (if we have one).

        On success, the dialog's `export_completed` / `import_completed`
        signals update the config + db; we don't have to do anything
        special here, but we DO need to refresh the "Last backup" line
        after the dialog closes so the user sees their new timestamp.
        """
        if self._db_provider is None:
            QMessageBox.information(
                self,
                "Backup unavailable",
                "No database is connected. (This Settings dialog was\n"
                "opened without a live db. Bug?)\n\n"
                "Use the Tray \u25b6 Export\u2026 menu instead.",
            )
            return
        db = self._db_provider()
        if db is None:
            QMessageBox.information(
                self,
                "Backup unavailable",
                "No database is connected. Try the Tray \u25b6 Export\u2026 menu.",
            )
            return
        # We import lazily to keep the settings module free of any
        # import cycle with ui/backup.py
        from snippy.ui.backup import BackupDialog
        # The dialog's "last_backup" display is driven by the dialog's
        # own `last_backup_provider`. We pass a callable that reads
        # from `self._working.backup.last_backup_at` \u2014 the dialog will
        # re-read it on construction, so changes from a successful
        # export inside this dialog are NOT reflected until the dialog
        # is reopened. That's a known v0.3.0 limitation; the refresh
        # after-close code below mitigates it.
        dlg = BackupDialog(
            self,
            db,
            last_backup_provider=lambda: self._working.backup.last_backup_at,
        )
        try:
            dlg.exec()
        finally:
            dlg.deleteLater()
        # Re-populate the Backup tab so the user sees the new timestamp
        # if they exported just now.
        self._populate_backup_from_config()
        self._lbl_dialog_status.setText("Dialog closed. Use Tray \u25b6 Export\u2026 next time too.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _copy_config(cfg: SnippyConfig) -> SnippyConfig:
    """Return a deep copy of the config so cancel/apply can be undone safely."""
    from dataclasses import replace
    from copy import deepcopy
    return replace(
        cfg,
        capture=deepcopy(cfg.capture),
        ui=deepcopy(cfg.ui),
        feedback=deepcopy(cfg.feedback),
        sync=deepcopy(cfg.sync),
        backup=deepcopy(cfg.backup),
    )
