"""Tests for the Snippet detail dialog (Phase 2 WS4)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication(sys.argv)


def _make_snippet(content: str, type_: str = "text", id_: int = 1, **kw) -> "object":
    from snippy.core.db import Snippet, hash_content
    created_at = kw.get("created_at", "2026-01-01T00:00:00+00:00")
    last_used_at = kw.get("last_used_at")
    use_count = kw.get("use_count", 0)
    is_pinned = kw.get("is_pinned", False)
    is_archived = kw.get("is_archived", False)
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
# Transforms
# ---------------------------------------------------------------------------


def test_transform_url_decoded_decodes_percent_sequences() -> None:
    from snippy.ui.detail import _transform_url_decoded
    assert _transform_url_decoded("hello%20world%21") == "hello world!"


def test_transform_url_decoded_keeps_valid_input() -> None:
    from snippy.ui.detail import _transform_url_decoded
    assert _transform_url_decoded("plain text") == "plain text"


def test_transform_json_pretty_pretty_prints_object() -> None:
    from snippy.ui.detail import _transform_json_pretty
    out = _transform_json_pretty('{"b":1,"a":2}')
    # sorted keys, indent=2
    assert out == '{\n  "a": 2,\n  "b": 1\n}'


def test_transform_json_pretty_raises_on_garbage() -> None:
    from snippy.ui.detail import _transform_json_pretty
    with pytest.raises(ValueError):
        _transform_json_pretty("not json {{{")


def test_transform_base64_decode() -> None:
    from snippy.ui.detail import _transform_base64_decode
    assert _transform_base64_decode("aGVsbG8=") == "hello"


def test_transform_base64_tolerates_whitespace_and_padding() -> None:
    from snippy.ui.detail import _transform_base64_decode
    assert _transform_base64_decode("aGVs bG8") == "hello"  # no padding, has spaces


def test_transform_lines_to_bullets() -> None:
    from snippy.ui.detail import _transform_lines_to_bullets
    out = _transform_lines_to_bullets("one\ntwo\n\nthree\n")
    assert out == "\u2022 one\n\u2022 two\n\u2022 three"


def test_transform_lines_to_bullets_empty_returns_input() -> None:
    from snippy.ui.detail import _transform_lines_to_bullets
    assert _transform_lines_to_bullets("") == ""


def test_transform_strip() -> None:
    from snippy.ui.detail import _transform_strip
    assert _transform_strip("  hello  \n  world  \n") == "hello\nworld"


def test_all_transforms_are_registered() -> None:
    from snippy.ui.detail import _TRANSFORMS
    expected = {"URL-decoded", "JSON pretty-print", "Base64 decode", "Lines \u2192 bullets", "Strip whitespace"}
    assert set(_TRANSFORMS.keys()) == expected


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------


def test_dialog_displays_content_and_metadata(qt_app: QApplication) -> None:
    from snippy.ui.detail import SnippetDetailDialog
    snip = _make_snippet("hello world", "text", id_=42, is_pinned=True, use_count=7)
    dlg = SnippetDetailDialog(snip)
    assert dlg._editor.toPlainText() == "hello world"
    assert dlg._meta_id.text() == "42"
    assert dlg._meta_uses.text() == "7"
    assert dlg._meta_pinned.text() == "\u2605 yes"


def test_dialog_open_url_button_visibility(qt_app: QApplication) -> None:
    from snippy.ui.detail import SnippetDetailDialog
    dlg_text = SnippetDetailDialog(_make_snippet("plain text", "text"))
    assert dlg_text._btn_open_url.isVisibleTo(dlg_text) is False
    dlg_url = SnippetDetailDialog(_make_snippet("https://x.com", "url"))
    # QPushButton.isVisibleTo() reflects setVisible(False/True) without
    # needing the parent window to actually be shown (offscreen-friendly).
    assert dlg_url._btn_open_url.isVisibleTo(dlg_url) is True


def test_edit_toggle_unlocks_editor(qt_app: QApplication) -> None:
    from snippy.ui.detail import SnippetDetailDialog
    dlg = SnippetDetailDialog(_make_snippet("hello", "text"))
    assert dlg._editor.isReadOnly() is True
    dlg._btn_edit.setChecked(True)
    assert dlg._editor.isReadOnly() is False
    assert dlg._btn_apply.isEnabled() is True


def test_apply_edits_emits_saved_with_new_content(qt_app: QApplication) -> None:
    from snippy.ui.detail import SnippetDetailDialog
    dlg = SnippetDetailDialog(_make_snippet("old text", "text"))
    saved: list = []
    dlg.snippet_saved.connect(saved.append)
    dlg._editor.setPlainText("new text")
    dlg._btn_edit.setChecked(True)
    dlg._on_apply_edits()
    assert len(saved) == 1
    assert saved[0].content == "new text"
    assert saved[0].id == dlg._original.id  # id preserved
    # Back to read-only
    assert dlg._editor.isReadOnly() is True


def test_apply_edits_no_op_when_unchanged(qt_app: QApplication) -> None:
    from snippy.ui.detail import SnippetDetailDialog
    dlg = SnippetDetailDialog(_make_snippet("hello", "text"))
    saved: list = []
    dlg.snippet_saved.connect(saved.append)
    dlg._btn_edit.setChecked(True)
    dlg._on_apply_edits()
    assert saved == []


def test_delete_emits_signal_when_confirmed(qt_app: QApplication) -> None:
    from snippy.ui.detail import SnippetDetailDialog
    dlg = SnippetDetailDialog(_make_snippet("x", "text", id_=99))
    dlg.set_confirm_fn(lambda *args, **kwargs: True)  # always-yes stub
    deleted: list = []
    dlg.snippet_deleted.connect(deleted.append)
    dlg._on_delete()
    assert deleted == [99]


def test_delete_no_emit_when_cancelled(qt_app: QApplication) -> None:
    from snippy.ui.detail import SnippetDetailDialog
    dlg = SnippetDetailDialog(_make_snippet("x", "text", id_=99))
    dlg.set_confirm_fn(lambda *args, **kwargs: False)  # always-no stub
    deleted: list = []
    dlg.snippet_deleted.connect(deleted.append)
    dlg._on_delete()
    assert deleted == []


def test_copy_writes_to_clipboard(qt_app: QApplication) -> None:
    from snippy.ui.detail import SnippetDetailDialog
    dlg = SnippetDetailDialog(_make_snippet("hello world", "text"))
    dlg._on_copy()
    assert QApplication.clipboard().text() == "hello world"
    assert "Copied" in dlg._status.text()


def test_copy_as_transform_writes_to_clipboard_and_handles_errors(qt_app: QApplication) -> None:
    from snippy.ui.detail import SnippetDetailDialog
    dlg = SnippetDetailDialog(_make_snippet("aGVsbG8=", "text"))
    dlg._do_transform("Base64 decode")
    assert QApplication.clipboard().text() == "hello"
    assert "Base64 decode" in dlg._status.text()

    # Error path: invalid JSON
    dlg._editor.setPlainText("not json")
    dlg._do_transform("JSON pretty-print")
    assert "JSON pretty-print" in dlg._status.text() and "failed" in dlg._status.text()


def test_open_url_invokes_desktop_services(qt_app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    from snippy.ui import detail as detail_mod
    from snippy.ui.detail import SnippetDetailDialog
    from PySide6.QtCore import QUrl

    captured: list = []
    # Stub accepts whatever was passed; normalize QUrl -> string for the assert.
    def _capture(url):
        captured.append(url.toString() if isinstance(url, QUrl) else str(url))
        return True
    monkeypatch.setattr(detail_mod.QDesktopServices, "openUrl", _capture)
    dlg = SnippetDetailDialog(_make_snippet("https://example.com", "url"))
    dlg._on_open_url()
    assert len(captured) == 1
    assert captured[0] == "https://example.com"


def test_open_url_no_op_for_empty(qt_app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    from snippy.ui import detail as detail_mod
    from snippy.ui.detail import SnippetDetailDialog

    captured: list = []
    monkeypatch.setattr(
        detail_mod.QDesktopServices, "openUrl", lambda url: captured.append(url) or True
    )
    dlg = SnippetDetailDialog(_make_snippet("   ", "text"))
    dlg._on_open_url()
    assert captured == []
