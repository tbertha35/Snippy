"""Backup / Restore dialog \u2014 Phase 3a (encrypted `.snip` bundles)."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from snippy.core.bundle import (
    BundleError,
    BundleFormatError,
    ImportResult,
    Manifest,
    export_bundle,
    import_bundle,
)
from snippy.core.db import Database


LOGGER = logging.getLogger(__name__)


def peek_manifest(path: str) -> Manifest:
    """Return just the Manifest from a `.snip` file (no DB reads, no decrypt).

    Used by the Backup dialog to detect `manifest.encrypted` before asking
    for a passphrase. Raises `BundleFormatError` on a corrupt / missing
    manifest, and the underlying `zipfile.BadZipFile` on a non-zip file.
    """
    import zipfile
    with zipfile.ZipFile(path, "r") as zf:
        return Manifest.from_json(zf.read("manifest.json").decode("utf-8"))


class BackupDialog(QDialog):
    """A small dialog with three big buttons: Export, Import, Schedule.

    The Schedule button is a placeholder for v0.3.1+; in v0.3.0 the
    backup scheduler is handled by `core.scheduler` (which fires a
    non-modal reminder toast when the user hasn't backed up in 7 days).
    """

    export_completed = Signal(str, int)     # path, snippet_count
    import_completed = Signal(ImportResult)

    def __init__(
        self,
        parent: QWidget | None,
        db: Database,
        *,
        last_backup_provider: Callable[[], str | None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("backup_dialog")
        self.setWindowTitle("Snippy \u2014 Backup & Restore")
        self.setModal(True)
        self.resize(520, 280)
        self._db = db
        self._last_backup_provider = last_backup_provider or (lambda: None)
        self._build_ui()

    # -- UI -------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        intro = QLabel(
            "Back up your snippets to a single `.snip` file.\n"
            "Optionally protect it with a passphrase (AES-256)."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._passphrase_edit = QLineEdit(self)
        self._passphrase_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._passphrase_edit.setPlaceholderText("Passphrase (optional, leave blank for no encryption)")
        layout.addWidget(self._passphrase_edit)

        self._show_pw = QCheckBox("Show passphrase", self)
        self._show_pw.toggled.connect(self._on_show_pw_toggled)
        layout.addWidget(self._show_pw)

        # Three big buttons
        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        self._btn_export = QPushButton("\U0001f4e4  Export to file\u2026", self)
        self._btn_import = QPushButton("\U0001f4e5  Import from file\u2026", self)
        self._btn_schedule = QPushButton("\u23f0  Schedule auto\u2026", self)
        for b in (self._btn_export, self._btn_import, self._btn_schedule):
            b.setMinimumHeight(40)
            button_row.addWidget(b)
        self._btn_export.clicked.connect(self._on_export)
        self._btn_import.clicked.connect(self._on_import)
        self._btn_schedule.clicked.connect(self._on_schedule)
        layout.addLayout(button_row)

        # Last-backup status line
        self._status = QLabel(self)
        self._status.setObjectName("status")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        self._refresh_status()

        # Close button
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        layout.addWidget(bb)

    def _on_show_pw_toggled(self, checked: bool) -> None:
        self._passphrase_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )

    def _refresh_status(self) -> None:
        last = self._last_backup_provider() or "never"
        self._status.setText(f"Last backup: {last}")

    # -- actions --------------------------------------------------------

    def _on_export(self) -> None:
        default = str(Path.home() / "Documents" / f"snippy-backup.bundle.snip")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export backup to", default, "Snippy bundles (*.snip)"
        )
        if not path:
            return
        if not path.endswith(".snip"):
            path += ".snip"
        passphrase = self._passphrase_edit.text() or None
        try:
            manifest = export_bundle(path, self._db, passphrase=passphrase)
        except OSError as exc:
            LOGGER.exception("Export failed: OS error")
            QMessageBox.critical(self, "Export failed", f"Could not write file:\n{exc}")
            return
        except Exception as exc:  # very defensive \u2014 bundle.write_zip is pretty safe
            LOGGER.exception("Export failed")
            QMessageBox.critical(self, "Export failed", f"Unexpected error:\n{exc}")
            return
        QMessageBox.information(
            self,
            "Backup complete",
            f"Exported {manifest.snippet_count} snippet(s) and "
            f"{manifest.tag_count} tag(s) to:\n{path}"
            + ("\n\n(Encrypted with passphrase.)" if manifest.encrypted else ""),
        )
        self._refresh_status()
        self.export_completed.emit(path, manifest.snippet_count)

    def _on_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import backup from", str(Path.home() / "Documents"),
            "Snippy bundles (*.snip)",
        )
        if not path:
            return
        # Peek at the manifest to detect encryption + give a clear error if
        # the file is corrupt before we ask for a passphrase.
        try:
            manifest = peek_manifest(path)
        except (BundleFormatError, KeyError, OSError, zipfile.BadZipFile) as exc:
            QMessageBox.warning(
                self, "Import failed",
                f"Could not read bundle:\n{exc}\n\n"
                "The file may be corrupt or in the wrong format."
            )
            return
        passphrase: str | None = None
        if manifest.encrypted:
            pw, ok = QInputDialog.getText(
                self, "Passphrase required",
                "This bundle is encrypted. Enter the passphrase:",
                QLineEdit.EchoMode.Password,
            )
            if not ok or not pw:
                return
            passphrase = pw
        try:
            result = import_bundle(path, self._db, passphrase=passphrase, mode="merge")
        except BundleError as exc:
            LOGGER.exception("Import failed")
            QMessageBox.critical(self, "Import failed", f"{exc}")
            return
        # Build the result dialog
        msg = result.summary()
        if not result.ok:
            msg += "\n\nSome snippets had errors (check the log for details):\n"
            msg += "\n".join(f"\u2022 {e}" for e in result.errors[:5])
            if len(result.errors) > 5:
                msg += f"\n\u2026 and {len(result.errors) - 5} more."
            QMessageBox.warning(self, "Import complete (with errors)", msg)
        else:
            QMessageBox.information(self, "Import complete", msg)
        self._refresh_status()
        self.import_completed.emit(result)

    def _on_schedule(self) -> None:
        # Phase 3a: scheduler is automatic (fires on app start when stale).
        # The full auto-export-to-folder flow lands in v0.3.1.
        QMessageBox.information(
            self, "Auto-backup schedule",
            "Snippy will remind you to back up every 7 days.\n\n"
            "Fully automatic background export is coming in v0.3.1.",
        )