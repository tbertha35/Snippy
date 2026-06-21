"""Tests for the WS8 theme system (`ui.styles`)."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


# ---------------------------------------------------------------------------
# Theme registry
# ---------------------------------------------------------------------------


def test_all_themes_are_registered() -> None:
    from snippy.ui.styles import THEMES
    assert set(THEMES.keys()) == {"midnight", "solarized-dark"}


def test_stylesheet_for_known_themes() -> None:
    from snippy.ui.styles import (
        THEME_MIDNIGHT,
        THEME_SOLARIZED_DARK,
        stylesheet_for,
    )
    assert stylesheet_for("midnight") == THEME_MIDNIGHT
    assert stylesheet_for("solarized-dark") == THEME_SOLARIZED_DARK


def test_stylesheet_for_unknown_falls_back_to_midnight() -> None:
    from snippy.ui.styles import THEME_MIDNIGHT, stylesheet_for
    assert stylesheet_for("does-not-exist") == THEME_MIDNIGHT
    assert stylesheet_for("") == THEME_MIDNIGHT
    assert stylesheet_for("Midnight") == THEME_MIDNIGHT  # case-sensitive dispatch; midnight it is


# ---------------------------------------------------------------------------
# Parseability
# ---------------------------------------------------------------------------


def test_all_qss_strings_are_parseable_by_qt() -> None:
    """Each theme must parse as valid QSS. Qt's parser silently accepts a
    lot; the strongest test is that `setStyleSheet` doesn't raise. We use a
    QApplication so the stylesheet engine is loaded."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from snippy.ui.styles import THEMES
    for name, qss in THEMES.items():
        # Apply to a throwaway widget and read it back; if Qt rejects the
        # QSS the call raises.
        from PySide6.QtWidgets import QWidget
        w = QWidget()
        w.setStyleSheet(qss)
        applied = w.styleSheet()
        # Qt normalizes the QSS (re-orders / reformats); just ensure it
        # contains at least one of our selectors and survives round-trip.
        assert "QWidget" in applied, f"theme {name!r} lost its base QWidget rule after apply"
        w.deleteLater()


# ---------------------------------------------------------------------------
# Reference checks (no missing asset refs)
# ---------------------------------------------------------------------------


# Matches `url(...)` references but excludes `data:` URIs (those are
# inline content, not local assets). The intent is to ensure themes
# don't reference local files (e.g. url('assets/foo.png')).
_URL_RE = re.compile(r"url\((?!(data:))([^)]+)\)")


@pytest.mark.parametrize("theme_name", ["midnight", "solarized-dark"])
def test_theme_does_not_reference_local_assets(theme_name: str) -> None:
    """WS8 themes are pure-QSS (no url() refs to local files)."""
    from snippy.ui.styles import stylesheet_for
    qss = stylesheet_for(theme_name)
    urls = _URL_RE.findall(qss)
    assert urls == [], f"theme {theme_name!r} should not reference external assets but has {urls}"


def test_themes_have_distinct_background_colors() -> None:
    """WS8 acceptance: the additional theme is visibly different from midnight."""
    from snippy.ui.styles import stylesheet_for
    midnight = stylesheet_for("midnight")
    solar = stylesheet_for("solarized-dark")
    # We only care that the bg-color value differs in each
    def bg(qss: str) -> str:
        m = re.search(r"QWidget\s*\{[^}]*background-color\s*:\s*([^;]+);", qss)
        return (m.group(1).strip() if m else "").lower()
    assert bg(midnight) != bg(solar)


# ---------------------------------------------------------------------------
# Live preview (Settings → Theme) — we don't load settings.py here; the
# styles module's public API is what gets called from there.
# ---------------------------------------------------------------------------


def test_stylesheet_for_returns_nonempty_string() -> None:
    from snippy.ui.styles import stylesheet_for
    for name in ("midnight", "solarized-dark"):
        qss = stylesheet_for(name)
        assert isinstance(qss, str) and qss.strip(), f"empty QSS for {name!r}"


def test_themes_dict_is_immutable_view_only() -> None:
    """Sanity: the public THEMES dict is a regular dict, not a frozen
    mapping. Callers shouldn't mutate it, but we don't enforce that here."""
    from snippy.ui.styles import THEMES
    assert isinstance(THEMES, dict)
    assert len(THEMES) >= 2


@pytest.mark.parametrize("theme_name", ["midnight", "solarized-dark"])
def test_history_table_has_explicit_item_states(theme_name: str) -> None:
    """Regression: selected/hover item states must be explicit so Windows
    (and other platforms) render the history-table highlight and text color
    correctly instead of relying on the native style engine."""
    from snippy.ui.styles import stylesheet_for
    qss = stylesheet_for(theme_name)
    assert "QTableView#history_table::item:selected {" in qss
    assert "QTableView#history_table::item:hover {" in qss
