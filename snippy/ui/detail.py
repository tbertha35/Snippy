"""Snippet detail / edit / transform dialog \u2014 Phase 2 WS4.

A modal `QDialog` that shows the full content of a single `Snippet` in a
monospace `QPlainTextEdit`, plus a row of action buttons:

    **Copy**           copy current (possibly edited) text to clipboard
    **Open URL**       open in OS browser (URL type only)
    **Edit**           toggle read-only \u2194 editable
    **Copy as\u2026**     submenu: URL-decoded, JSON pretty-print, Base64 decode,
                       lines \u2192 bullets, strip whitespace
    **Delete\u2026**      hard-delete with confirmation
    **Close**

Reachable from:
- History window right-click \u2192 **Details\u2026**
- Palette `Ctrl+D` on the selected row (wired in `app.py`)

The dialog is non-singleton: each invocation creates a fresh instance and
the parent owns it. Edit-mode changes are kept in-memory only; saving them
back to the DB is wired via the `snippet_saved` signal so the host can
choose to call `db.update_snippet_content(id, new_text)`.
"""
from __future__ import annotations

import base64
import json
import logging
import urllib.parse
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDesktopServices, QFont, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from snippy import __app_name__, __version__
from snippy.core.db import Snippet
from snippy.core.detector import color_for, icon_for


LOGGER = logging.getLogger(__name__)


def _transform_url_decoded(s: str) -> str:
    try:
        return urllib.parse.unquote(s)
    except Exception:
        return s


def _transform_json_pretty(s: str) -> str:
    try:
        parsed = json.loads(s)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Not valid JSON: {exc}") from exc
    return json.dumps(parsed, indent=2, ensure_ascii=False, sort_keys=True)


def _transform_base64_decode(s: str) -> str:
    # Tolerate whitespace and missing padding
    cleaned = "".join(s.split())
    pad = (-len(cleaned)) % 4
    cleaned += "=" * pad
    try:
        raw = base64.b64decode(cleaned, validate=False)
    except Exception as exc:
        raise ValueError(f"Not valid Base64: {exc}") from exc
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace")


def _transform_lines_to_bullets(s: str) -> str:
    lines = [ln for ln in s.splitlines() if ln.strip()]
    if not lines:
        return s
    return "\n".join(f"\u2022 {ln.strip()}" for ln in lines)


def _transform_strip(s: str) -> str:
    # Strip each line's ends and trim outer whitespace
    return "\n".join(ln.strip() for ln in s.splitlines()).strip()


_TRANSFORMS: dict[str, Callable[[str], str]] = {
    "URL-decoded":         _transform_url_decoded,
    "JSON pretty-print":   _transform_json_pretty,
    "Base64 decode":       _transform_base64_decode,
    "Lines \u2192 bullets":  _transform_lines_to_bullets,
    "Strip whitespace":    _transform_strip,
}


# Optional override hook for tests + future "auto-prompt" flows
ConfirmDialogFn = Callable[[QWidget, str, str], bool]
_default_confirm: ConfirmDialogFn = (
    lambda parent, title, msg: QMessageBox.question(
        parent, title, msg,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    ) == QMessageBox.StandardButton.Yes
)


# ---------------------------------------------------------------------------
# The dialog
# ---------------------------------------------------------------------------


class SnippetDetailDialog(QDialog):
    """Read-only-by-default monospace viewer with edit + transform + delete.

    Signals:
        snippet_deleted(int)  \u2014 emitted with the snippet id if the user confirms
        snippet_saved(Snippet) \u2014 emitted (text-only) if edit-mode changes were applied
    """

    snippet_deleted = Signal(int)
    snippet_saved = Signal(Snippet)  # carries the *original* row + new content
    # The actual save is the parent's job; we only emit.

    def __init__(self, snippet: Snippet, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._original = snippet
        self._working = snippet
        self._confirm = _default_confirm
        self.setObjectName("snippet_detail")
        self.setWindowTitle(f"{__app_name__} \u2014 Snippet #{snippet.id}")
        self.resize(720, 480)
        self._build_ui()
        self._populate()

    # -- test/host hooks --------------------------------------------------

    def set_confirm_fn(self, fn: ConfirmDialogFn) -> None:
        """Override the confirm dialog (used by tests to avoid modals)."""
        self._confirm = fn

    # -- UI build ---------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(10)

        # Header: icon, type, id, hash
        header = QHBoxLayout()
        header.setSpacing(8)
        icon_lbl = QLabel(icon_for(self._original.content_type), self)
        icon_lbl.setStyleSheet(f"font-size: 22px; color: {color_for(self._original.content_type)};")
        header.addWidget(icon_lbl)

        title = QLabel(f"Snippet #{self._original.id}  \u00b7  {self._original.content_type}", self)
        title_font = QFont(); title_font.setBold(True); title_font.setPointSize(13)
        title.setFont(title_font)
        header.addWidget(title)
        header.addStretch(1)
        outer.addLayout(header)

        # Metadata group
        meta = QGroupBox("Metadata", self)
        meta_form = QFormLayout(meta)
        meta_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._meta_id = QLabel(str(self._original.id), meta)
        self._meta_hash = QLabel(self._original.content_hash[:16] + "\u2026", meta)
        self._meta_created = QLabel(self._original.created_at, meta)
        self._meta_used = QLabel(self._original.last_used_at or "\u2014", meta)
        self._meta_uses = QLabel(str(self._original.use_count), meta)
        self._meta_pinned = QLabel("\u2605 yes" if self._original.is_pinned else "no", meta)

        meta_form.addRow("ID:", self._meta_id)
        meta_form.addRow("Hash:", self._meta_hash)
        meta_form.addRow("Created:", self._meta_created)
        meta_form.addRow("Last used:", self._meta_used)
        meta_form.addRow("Uses:", self._meta_uses)
        meta_form.addRow("Pinned:", self._meta_pinned)
        outer.addWidget(meta)

        # Content editor (read-only by default)
        self._editor = QPlainTextEdit(self)
        self._editor.setObjectName("content")
        self._editor.setReadOnly(True)
        mono = QFont("Consolas, Menlo, monospace")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._editor.setFont(mono)
        outer.addWidget(self._editor, stretch=1)

        # Action row
        actions = QHBoxLayout()
        actions.setSpacing(6)

        self._btn_copy = QPushButton("\U0001f4cb Copy", self)
        self._btn_copy.setShortcut(QKeySequence("Ctrl+C"))
        self._btn_copy.clicked.connect(self._on_copy)
        actions.addWidget(self._btn_copy)

        self._btn_copy_as = QPushButton("\u2398 Copy as\u2026", self)
        self._btn_copy_as.clicked.connect(self._on_copy_as)
        actions.addWidget(self._btn_copy_as)

        self._btn_open_url = QPushButton("\U0001f517 Open URL", self)
        self._btn_open_url.setVisible(self._original.content_type == "url")
        self._btn_open_url.clicked.connect(self._on_open_url)
        actions.addWidget(self._btn_open_url)

        actions.addStretch(1)

        self._btn_edit = QPushButton("\u270f\ufe0f Edit", self)
        self._btn_edit.setCheckable(True)
        self._btn_edit.toggled.connect(self._on_edit_toggled)
        actions.addWidget(self._btn_edit)

        self._btn_apply = QPushButton("\u2713 Apply edits", self)
        self._btn_apply.setEnabled(False)
        self._btn_apply.clicked.connect(self._on_apply_edits)
        actions.addWidget(self._btn_apply)

        self._btn_delete = QPushButton("\U0001f5d1\ufe0f Delete\u2026", self)
        self._btn_delete.clicked.connect(self._on_delete)
        actions.addWidget(self._btn_delete)

        close_btn = QPushButton("Close", self)
        close_btn.clicked.connect(self.accept)
        actions.addWidget(close_btn)

        outer.addLayout(actions)

        # Status bar
        self._status = QLabel("", self)
        self._status.setObjectName("status")
        self._status.setStyleSheet("color: gray;")
        outer.addWidget(self._status)

    # -- behavior --------------------------------------------------------

    def _populate(self) -> None:
        self._editor.setPlainText(self._working.content)
        self._status.setText(f"{len(self._working.content):,} chars \u00b7 {self._editor.blockCount():,} lines")

    def current_text(self) -> str:
        """Return the editor's current text (post-edits, if any)."""
        return self._editor.toPlainText()

    # -- slots ------------------------------------------------------------

    def _on_copy(self) -> None:
        QApplication.clipboard().setText(self.current_text())
        self._status.setText("Copied to clipboard.")

    def _on_copy_as(self) -> None:
        menu = QMenu(self)
        for name in _TRANSFORMS:
            act = menu.addAction(name)
            act.triggered.connect(lambda _checked=False, n=name: self._do_transform(n))
        menu.exec(self._btn_copy_as.mapToGlobal(self._btn_copy_as.rect().bottomLeft()))

    def _do_transform(self, name: str) -> None:
        fn = _TRANSFORMS[name]
        try:
            result = fn(self.current_text())
        except Exception as exc:
            self._status.setText(f"\u26a0 {name} failed: {exc}")
            return
        # Show the transform in a small popup so the user can copy/paste
        QApplication.clipboard().setText(result)
        preview = result if len(result) <= 80 else result[:77] + "\u2026"
        self._status.setText(f"{name} \u2192 clipboard ({len(result):,} chars). Preview: {preview!r}")

    def _on_open_url(self) -> None:
        text = self.current_text().strip()
        if not text:
            return
        QDesktopServices.openUrl(text)
        self._status.setText(f"Opened {text!r} in browser.")

    def _on_edit_toggled(self, on: bool) -> None:
        self._editor.setReadOnly(not on)
        self._btn_apply.setEnabled(on)
        self._status.setText("Editing \u2014 click Apply to save." if on else "Read-only.")

    def _on_apply_edits(self) -> None:
        new_text = self.current_text()
        if new_text == self._original.content:
            self._status.setText("No changes to apply.")
            return
        # We rebuild a Snippet with the new content but keep everything else
        from dataclasses import replace
        self._working = replace(self._working, content=new_text)
        self.snippet_saved.emit(self._working)
        self._status.setText("Edits applied (host saved to DB).")
        # Drop back to read-only
        self._btn_edit.setChecked(False)

    def _on_delete(self) -> None:
        ok = self._confirm(
            self,
            "Delete snippet",
            f"Permanently delete snippet #{self._original.id}? This cannot be undone.",
        )
        if not ok:
            return
        LOGGER.info("User confirmed delete of snippet #%d", self._original.id)
        self.snippet_deleted.emit(self._original.id)
        self.accept()