"""QSS theming for Snippy.

Bundled themes live as constants here for Phase 1 (no external `.qss`
files yet). Phase 2 will load them from `snippy/assets/themes/`.

The default theme is a dark, slightly desaturated palette inspired by
modern editor palettes. It's easy on the eyes for the always-visible
tray tooltip and pops nicely as a toast.
"""
from __future__ import annotations


# Default ("midnight") theme.
THEME_MIDNIGHT = """
QWidget {
    background-color: #0f172a;
    color: #e2e8f0;
    font-family: -apple-system, "Segoe UI", "Inter", "Helvetica Neue", sans-serif;
    font-size: 13px;
}

QLineEdit#search {
    background-color: #1e293b;
    border: 1px solid #334155;
    border-radius: 8px;
    padding: 8px 12px;
    selection-background-color: #3b82f6;
}

QListWidget#results {
    background-color: transparent;
    border: none;
    outline: 0;
}

QListWidget#results::item {
    padding: 8px 12px;
    border-radius: 6px;
}

QListWidget#results::item:selected {
    background-color: #1e40af;
    color: #f8fafc;
}

QLabel#typeIcon {
    font-size: 16px;
}

QLabel#preview {
    color: #cbd5e1;
}

QLabel#meta {
    color: #64748b;
    font-size: 11px;
}

/* History table — explicit alternate-row color so macOS doesn't use its
   light default and make every even row unreadable. */
QTableView#history_table {
    background-color: #0f172a;
    alternate-background-color: #1e293b;
    selection-background-color: #1e40af;
    selection-color: #f8fafc;
    gridline-color: transparent;
}
QTableView#history_table::item {
    padding: 4px 8px;
    border: none;
}
QTableView#history_table::item:selected {
    background-color: #1e40af;
    color: #f8fafc;
}
QTableView#history_table::item:hover {
    background-color: #334155;
    color: #f8fafc;
}

/* Toast */
QFrame#toast {
    background-color: #1e293b;
    border: 1px solid #334155;
    border-left: 4px solid #3b82f6;  /* overridden per-type in feedback.py */
    border-radius: 8px;
}

QLabel#toastTitle {
    color: #f8fafc;
    font-weight: 600;
}

QLabel#toastBody {
    color: #cbd5e1;
}

/* About dialog (v0.3.0) — explicit readable colors in the app theme */
QDialog#aboutDialog {
    background-color: #0f172a;
    color: #e2e8f0;
}
QLabel#aboutTitle {
    color: #f8fafc;
    font-size: 18px;
    font-weight: 700;
}
QLabel#aboutSubtitle {
    color: #cbd5e1;
    font-size: 13px;
    font-weight: 500;
}
QFrame#aboutCard {
    background-color: #1e293b;
    border: 1px solid #334155;
    border-radius: 8px;
}
QLabel#aboutBody {
    color: #cbd5e1;
    font-size: 12px;
    line-height: 1.5em;
}
QLabel#aboutLink {
    color: #3b82f6;
}
QLabel#aboutLink a {
    color: #60a5fa;
    text-decoration: none;
}
QLabel#aboutLink a:hover {
    text-decoration: underline;
}

/* PlaceholderWindow (the "status" window shown via tray menu).
   v0.3.0: explicit readable colors in the app theme. The previous
   inline `color: palette(mid)` in app.py was invalid QSS. */
QLabel#statusTitle {
    color: #f8fafc;
    font-size: 16px;
    font-weight: 700;
}
QLabel#statusBody {
    color: #cbd5e1;
    font-size: 12px;
    line-height: 1.5em;
}
"""


# --- WS8: additional theme — Solarized Dark -------------------

THEME_SOLARIZED_DARK: str = """
/* Solarized Dark — Ethan Schoonover's classic palette, dark variant */
QWidget              { background-color: #002b36; color: #93a1a1; font-family: 'Segoe UI', sans-serif; }
QLineEdit, QPlainTextEdit, QListView, QTableView {
    background-color: #073642; color: #eee8d5;
    border: 1px solid #586e75; border-radius: 4px; padding: 4px;
    selection-background-color: #b58900; selection-color: #002b36;
}
QPushButton          { background-color: #268bd2; color: #fdf6e3; border: none; border-radius: 4px; padding: 6px 14px; font-weight: 500; }
QPushButton:hover    { background-color: #2aa198; }
QPushButton:checked  { background-color: #cb4b16; color: #fdf6e3; }
QLabel#status, QLabel#meta { color: #657b83; }
QFrame#palette, QMainWindow#history { background-color: #002b36; }
QLabel[role="hint"]  { color: #93a1a1; }

/* About dialog (v0.3.0) */
QDialog#aboutDialog  { background-color: #002b36; color: #93a1a1; }
QLabel#aboutTitle    { color: #fdf6e3; font-size: 18px; font-weight: 700; }
QLabel#aboutSubtitle { color: #b58900; font-size: 13px; font-weight: 500; }
QFrame#aboutCard     { background-color: #073642; border: 1px solid #586e75; border-radius: 8px; }
QLabel#aboutBody     { color: #93a1a1; font-size: 12px; line-height: 1.5em; }
QLabel#aboutLink     { color: #268bd2; }
QLabel#aboutLink a   { color: #2aa198; text-decoration: none; }
QLabel#aboutLink a:hover { text-decoration: underline; }
QLabel#statusTitle    { color: #fdf6e3; font-size: 16px; font-weight: 700; }
QLabel#statusBody     { color: #93a1a1; font-size: 12px; line-height: 1.5em; }

/* History table — explicit alternate-row color for solarized-dark too. */
QTableView#history_table {
    background-color: #002b36;
    alternate-background-color: #073642;
    selection-background-color: #b58900;
    selection-color: #002b36;
    gridline-color: transparent;
}
QTableView#history_table::item {
    padding: 4px 8px;
    border: none;
}
QTableView#history_table::item:selected {
    background-color: #b58900;
    color: #002b36;
}
QTableView#history_table::item:hover {
    background-color: #586e75;
    color: #eee8d5;
}
"""

THEMES: dict[str, str] = {
    "midnight":       THEME_MIDNIGHT,
    "solarized-dark": THEME_SOLARIZED_DARK,
}


def stylesheet_for(theme: str) -> str:
    """Return the QSS for a named theme, falling back to `midnight`."""
    return THEMES.get(theme, THEME_MIDNIGHT)