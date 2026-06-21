"""Generate snippy.ico from appimage.png.

Run this whenever `appimage.png` is replaced with a new design. The
resulting multi-resolution `.ico` is what Windows looks for when it
renders the taskbar / Alt-Tab / title-bar icons for the Snippy
executable. Without this file embedded in the `.exe` (via
`pyinstaller.spec`'s `icon=` field), Windows falls back to the
generic Python icon and the taskbar entry shows a blank page.

Usage:
    python snippy/assets/generate_ico.py

Output: snippy/assets/snippy.ico (multi-resolution, 16x16 \u2013 256x256)

This is intentionally a tiny script (not a full click-to-generate GUI)
because it's a one-time build-step helper. CI and developer machines
should both be able to call it via `python -m` or directly.
"""
from __future__ import annotations

from pathlib import Path

try:
    from PIL import Image
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Pillow is required to regenerate snippy.ico.\n"
        "Install it with:  pip install Pillow"
    ) from exc


HERE = Path(__file__).resolve().parent
SRC = HERE / "appimage.png"
DST = HERE / "snippy.ico"

# Windows uses 16, 24, 32, 48, 64, 128, 256. 256 is the Vista+ "large
# icon" size; older Windows may pick 128 or 48 depending on DPI.
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def main() -> int:
    if not SRC.is_file():
        raise SystemExit(f"Source PNG not found: {SRC}")
    img = Image.open(SRC).convert("RGBA")
    # Pillow writes all sizes into a single multi-resolution .ico.
    img.save(DST, format="ICO", sizes=SIZES)
    print(f"Wrote {DST} ({DST.stat().st_size} bytes, sizes={SIZES})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())