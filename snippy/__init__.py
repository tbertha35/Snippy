"""Snippy — smart clipboard & snippet manager."""

from __future__ import annotations

import sys
from pathlib import Path

__version__ = "0.3.0"
__app_name__ = "Snippy"


def assets_dir() -> str:
    """Return the absolute path to the bundled `snippy/assets` directory.

    Works in four modes:
    - **dev** (`python -m snippy`): uses the in-source `snippy/assets/`.
    - **PyInstaller frozen one-folder**: uses `sys._MEIPASS / snippy/assets`.
    - **PyInstaller frozen .app bundle**: uses
      `sys._MEIPASS / _internal / snippy / assets`, because the launcher
      executable lives in `Contents/MacOS/` while the PyInstaller payload
      is in `Contents/MacOS/_internal/`.
    - **zipapp / wheel**: uses the import-time `__file__` of this module.

    Always returns a string (not a Path) so callers can pass it
    directly to `QPixmap(...)` and `QIcon(...)` without coercion.
    """
    # 1) PyInstaller frozen
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        # Standard one-folder layout: <meipass>/snippy/assets
        candidate = Path(meipass) / "snippy" / "assets"
        if candidate.is_dir():
            return str(candidate)
        # macOS .app bundle layout: executable in MacOS/, payload in MacOS/_internal/
        candidate = Path(meipass) / "_internal" / "snippy" / "assets"
        if candidate.is_dir():
            return str(candidate)
    # 2) Import-time location
    candidate = Path(__file__).resolve().parent / "assets"
    if candidate.is_dir():
        return str(candidate)
    # 3) Last-resort: current working dir (for legacy zipapp builds)
    return str(Path.cwd() / "snippy" / "assets")
