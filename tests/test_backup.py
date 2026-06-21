"""Smoke tests for the Backup / Restore dialog (`ui.backup`).

The heavy lifting (round-trip, encryption, merge semantics) is covered
by `tests/test_bundle.py`. These tests verify the dialog wires up
correctly, exposes the expected signals, and that `peek_manifest`
behaves on a real bundle file.
"""
from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication(sys.argv)


# ---------------------------------------------------------------------------
# peek_manifest
# ---------------------------------------------------------------------------


def test_peek_manifest_plain(tmp_path: Path) -> None:
    from snippy.core.bundle import export_bundle
    from snippy.core.db import Database
    from snippy.ui.backup import peek_manifest
    db_path = tmp_path / "x.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(path=db_path)
    db.add_snippet("hi", "text")
    out = tmp_path / "x.snip"
    export_bundle(out, db)
    m = peek_manifest(str(out))
    assert m.encrypted is False
    assert m.snippet_count == 1


def test_peek_manifest_encrypted(tmp_path: Path) -> None:
    from snippy.core.bundle import export_bundle
    from snippy.core.db import Database
    from snippy.ui.backup import peek_manifest
    db_path = tmp_path / "x.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(path=db_path)
    db.add_snippet("hi", "text")
    out = tmp_path / "x.snip"
    export_bundle(out, db, passphrase="hunter2")
    m = peek_manifest(str(out))
    assert m.encrypted is True
    assert m.salt_b64 is not None


def test_peek_manifest_on_non_zip_raises(tmp_path: Path) -> None:
    from snippy.ui.backup import peek_manifest
    out = tmp_path / "x.snip"
    out.write_bytes(b"not a zip file")
    with pytest.raises((zipfile.BadZipFile, KeyError, Exception)):
        peek_manifest(str(out))


# ---------------------------------------------------------------------------
# BackupDialog smoke
# ---------------------------------------------------------------------------


def test_dialog_starts_with_three_buttons(qt_app: QApplication, tmp_path: Path) -> None:
    from snippy.core.db import Database
    from snippy.ui.backup import BackupDialog
    db_path = tmp_path / "x.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(path=db_path)
    db.add_snippet("hello", "text")

    dlg = BackupDialog(parent=None, db=db)
    assert dlg._btn_export is not None
    assert dlg._btn_import is not None
    assert dlg._btn_schedule is not None
    assert dlg._btn_export.text() != ""
    assert dlg._btn_import.text() != ""
    assert dlg._btn_schedule.text() != ""
    dlg.deleteLater()


def test_dialog_status_uses_provider(qt_app: QApplication, tmp_path: Path) -> None:
    from snippy.core.db import Database
    from snippy.ui.backup import BackupDialog
    db_path = tmp_path / "x.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(path=db_path)
    db.add_snippet("hello", "text")

    dlg = BackupDialog(
        parent=None, db=db, last_backup_provider=lambda: "yesterday"
    )
    assert "yesterday" in dlg._status.text()
    dlg.deleteLater()


def test_show_pw_toggle_changes_echomode(qt_app: QApplication, tmp_path: Path) -> None:
    from snippy.core.db import Database
    from snippy.ui.backup import BackupDialog
    db_path = tmp_path / "x.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(path=db_path)
    db.add_snippet("hello", "text")

    dlg = BackupDialog(parent=None, db=db)
    from PySide6.QtWidgets import QLineEdit
    assert dlg._passphrase_edit.echoMode() == QLineEdit.EchoMode.Password
    dlg._on_show_pw_toggled(True)
    assert dlg._passphrase_edit.echoMode() == QLineEdit.EchoMode.Normal
    dlg._on_show_pw_toggled(False)
    assert dlg._passphrase_edit.echoMode() == QLineEdit.EchoMode.Password
    dlg.deleteLater()


def test_export_completed_signal_fires_on_export(qt_app: QApplication, tmp_path: Path, monkeypatch) -> None:
    """Drive the export path with stub file dialogs so we don't need a display."""
    from snippy.core.db import Database
    from snippy.ui.backup import BackupDialog
    db_path = tmp_path / "x.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(path=db_path)
    db.add_snippet("hello", "text")

    # Stub the QFileDialog getSaveFileName to return our temp path
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    out_path = tmp_path / "out.snip"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        staticmethod(lambda *a, **k: (str(out_path), "Snippy bundles (*.snip)")),
    )
    monkeypatch.setattr(
        QMessageBox, "information",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok),
    )

    dlg = BackupDialog(parent=None, db=db)
    fired: list = []
    dlg.export_completed.connect(lambda p, n: fired.append((p, n)))
    dlg._on_export()
    assert fired and fired[0][0] == str(out_path)
    assert fired[0][1] == 1   # snippet_count
    assert out_path.exists() and zipfile.is_zipfile(out_path)
    dlg.deleteLater()


def test_import_completed_signal_fires_on_import(qt_app: QApplication, tmp_path: Path, monkeypatch) -> None:
    """Drive the import path with stubs."""
    from snippy.core.bundle import export_bundle, ImportResult
    from snippy.core.db import Database
    from snippy.ui.backup import BackupDialog
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    # Create a real bundle to import
    src_db = Database(path=tmp_path / "src.db")
    src_db.add_snippet("hello", "text")
    src_db.add_snippet("world", "text")
    bundle_path = tmp_path / "b.snip"
    export_bundle(bundle_path, src_db)

    # Create the destination db (empty)
    dst_db = Database(path=tmp_path / "dst.db")

    monkeypatch.setattr(
        QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (str(bundle_path), "Snippy bundles (*.snip)")),
    )
    monkeypatch.setattr(
        QMessageBox, "information",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok),
    )

    dlg = BackupDialog(parent=None, db=dst_db)
    fired: list[ImportResult] = []
    dlg.import_completed.connect(lambda r: fired.append(r))
    dlg._on_import()
    assert fired and fired[0].imported == 2 and dst_db.count() == 2
    dlg.deleteLater()