"""Tests for the screen-capture utilities (non-interactive parts)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication(sys.argv)



from snippy.core.capture_screen import (
    _grab_desktop,
    _logical_rect_to_device,
    capture_screen_region,
    default_image_save_dir,
    generate_image_filename,
    resolve_image_save_dir,
    save_captured_image,
)


def test_default_image_save_dir_returns_existing_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When a configured dir is provided and exists, use it."""
    configured = str(tmp_path / "screenshots")
    Path(configured).mkdir(parents=True, exist_ok=True)
    assert resolve_image_save_dir(configured) == Path(configured)


def test_resolve_image_save_dir_falls_back_to_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When configured dir is missing, fall back to the platform default."""
    monkeypatch.setattr("snippy.core.capture_screen.user_pictures_dir", lambda: str(tmp_path / "Pictures"))
    result = resolve_image_save_dir("/this/does/not/exist")
    assert result == tmp_path / "Pictures"


def test_generate_image_filename_has_png_extension() -> None:
    name = generate_image_filename()
    assert name.startswith("snippy_capture_")
    assert name.endswith(".png")
    assert len(name) > len("snippy_capture_.png")


def test_generate_image_filename_respects_prefix() -> None:
    name = generate_image_filename(prefix="custom")
    assert name.startswith("custom_")
    assert name.endswith(".png")


def test_save_captured_image_creates_file(tmp_path: Path, qt_app) -> None:
    from PySide6.QtGui import QPixmap

    pixmap = QPixmap(20, 20)
    path = save_captured_image(pixmap, directory=tmp_path, filename="test.png")
    assert path.exists()
    assert path.name == "test.png"


def test_save_captured_image_adds_png_extension(tmp_path: Path, qt_app) -> None:
    from PySide6.QtGui import QPixmap

    pixmap = QPixmap(20, 20)
    path = save_captured_image(pixmap, directory=tmp_path, filename="test")
    assert path.name == "test.png"


def test_logical_rect_to_device_scales_coordinates() -> None:
    """The helper must multiply logical widget coordinates by the DPR."""
    rect = QRect(10, 20, 30, 40)
    scaled = _logical_rect_to_device(rect, 1.5)
    assert scaled == QRect(15, 30, 45, 60)


class _FakeApp:
    """Stand-in for QApplication exposing only the bits capture_screen uses."""

    def __init__(self, screens, primary=None):
        self._screens = screens
        self._primary = primary or screens[0] if screens else None

    def screens(self):
        return self._screens

    def primaryScreen(self):
        return self._primary


class _FakeScreen:
    """Stand-in for QScreen so high-DPI capture can be tested offscreen."""

    def __init__(
        self,
        *,
        dpr: float,
        geometry: QRect,
        grab: QPixmap | None = None,
    ) -> None:
        self._dpr = dpr
        self._geometry = QRect(geometry)
        if grab is None:
            pm = QPixmap(
                int(geometry.width() * dpr),
                int(geometry.height() * dpr),
            )
            pm.fill(Qt.GlobalColor.red)
            self._grab = pm
        else:
            self._grab = grab

    def grabWindow(self, _wid: int) -> QPixmap:
        return self._grab

    def devicePixelRatio(self) -> float:
        return self._dpr

    def geometry(self) -> QRect:
        return QRect(self._geometry)


def test_grab_desktop_single_screen_2x(qt_app) -> None:
    """A single 2x screen at (0,0) returns a 200x200 composite."""
    screen = _FakeScreen(dpr=2.0, geometry=QRect(0, 0, 100, 100))
    fake_app = _FakeApp([screen])
    out = _grab_desktop(fake_app)
    assert out is not None
    composite, grabs, virtual, top_left = out
    assert virtual == QRect(0, 0, 100, 100)
    assert top_left == QPoint(0, 0)
    assert composite.width() == 200
    assert composite.height() == 200
    assert len(grabs) == 1
    assert grabs[0].dpr == 2.0


def test_grab_desktop_multi_monitor_unions_virtual(qt_app) -> None:
    """Two side-by-side screens: virtual = (0,0,200,100), top_left = (0,0)."""
    left = _FakeScreen(dpr=1.5, geometry=QRect(0, 0, 100, 100))
    right = _FakeScreen(dpr=1.5, geometry=QRect(100, 0, 100, 100))
    fake_app = _FakeApp([left, right])
    out = _grab_desktop(fake_app)
    assert out is not None
    composite, grabs, virtual, top_left = out
    assert virtual == QRect(0, 0, 200, 100)
    assert top_left == QPoint(0, 0)
    assert composite.width() == 300
    assert composite.height() == 150
    assert len(grabs) == 2


def test_grab_desktop_negative_origin(qt_app) -> None:
    """A secondary monitor at x=-1920 must normalize the virtual geom to start at 0."""
    primary = _FakeScreen(dpr=1.0, geometry=QRect(0, 0, 100, 100))
    secondary = _FakeScreen(dpr=1.0, geometry=QRect(-100, 0, 100, 100))
    fake_app = _FakeApp([primary, secondary])
    out = _grab_desktop(fake_app)
    assert out is not None
    composite, grabs, virtual, top_left = out
    # Union is (-100, 0, 200, 100) -> normalized (0, 0, 200, 100).
    assert virtual == QRect(0, 0, 200, 100)
    assert top_left == QPoint(-100, 0)
    assert composite.width() == 200
    assert composite.height() == 100


def test_capture_region_respects_device_pixel_ratio(
    tmp_path: Path, qt_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 2x screen should crop physical pixels, not logical pixels.

    The widget's devicePixelRatio is pinned to 2.0, so the selection rect
    emitted by `region_selected` is in *device* pixels of the composite.
    A 40x40 device-pixel selection on a 2x screen => 40x40 device crop.
    """
    screen = _FakeScreen(dpr=2.0, geometry=QRect(0, 0, 100, 100))
    fake_app = _FakeApp([screen])
    monkeypatch.setattr(
        "snippy.core.capture_screen._grab_desktop", lambda _app: _grab_desktop(fake_app),
    )
    monkeypatch.setattr("snippy.core.capture_screen.default_image_save_dir", lambda: tmp_path)

    selector = capture_screen_region(copy_to_clipboard=False)
    assert selector is not None

    # The widget covers the virtual geometry (100x100 logical). The
    # selection rect is in **logical** coords. A 40x40 logical selection
    # on a 2x screen => 80x80 device crop.
    selector.region_selected.emit(QRect(10, 10, 40, 40))

    saved = list(tmp_path.glob("*.png"))
    assert len(saved) == 1
    cropped = QPixmap(str(saved[0]))
    assert cropped.width() == 80
    assert cropped.height() == 80

    selector.close()


def test_region_selector_paints_composite_to_full_widget(qt_app) -> None:
    """The first paint must stretch the composite across the full widget.

    We build a 200x200 device-pixel composite (representing a 100x100 logical
    screen at 2x) filled with green, plus a 100x100 logical widget. The
    bottom-right corner of the rendered image must contain green (from the
    composite) — if the pixmap is only drawn in the top-left quarter, the
    bottom-right would still be black.
    """
    from snippy.core.capture_screen import RegionSelector

    composite = QPixmap(200, 200)
    composite.fill(Qt.GlobalColor.green)
    selector = RegionSelector(composite, device_pixel_ratio=2.0)
    # Widget is 100x100 logical (matches the underlying screen), and the
    # composite is 200x200 device pixels = 100x100 logical.
    selector.setGeometry(QRect(0, 0, 100, 100))

    img = QImage(100, 100, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.black)
    painter = QPainter(img)
    selector.render(painter, QPoint(0, 0))
    painter.end()

    # Sample the bottom-right and center; both should have non-black green
    # (modulated by the dim overlay). The key check is that the bottom-right
    # is *not* pure black — that would mean the composite only filled the
    # top-left quarter of the widget.
    center = img.pixelColor(50, 50)
    bottom_right = img.pixelColor(95, 95)
    for px, name in ((center, "center"), (bottom_right, "bottom-right")):
        # Anything other than pure black means the widget rendered there.
        assert (px.red(), px.green(), px.blue()) != (0, 0, 0), (
            f"{name} pixel is still black — the widget did not paint there"
        )

    selector.close()


def test_capture_region_handles_secondary_screen(
    tmp_path: Path, qt_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A selection on the *secondary* monitor must crop that monitor, not the primary."""
    primary = _FakeScreen(dpr=2.0, geometry=QRect(0, 0, 100, 100))
    # Fill the secondary grab with blue so we can verify it.
    blue = QPixmap(200, 200)
    blue.fill(Qt.GlobalColor.blue)
    secondary = _FakeScreen(
        dpr=2.0, geometry=QRect(100, 0, 100, 100), grab=blue,
    )
    fake_app = _FakeApp([primary, secondary])
    monkeypatch.setattr(
        "snippy.core.capture_screen._grab_desktop", lambda _app: _grab_desktop(fake_app),
    )
    monkeypatch.setattr("snippy.core.capture_screen.default_image_save_dir", lambda: tmp_path)

    selector = capture_screen_region(copy_to_clipboard=False)
    assert selector is not None

    # Secondary starts at x=100 in virtual coords; the overlay is normalized
    # to start at 0, so the secondary's x=100 maps to widget x=100.
    # Selection of 20x20 starting at (110, 20) -> 40x40 device crop, all blue.
    selector.region_selected.emit(QRect(110, 20, 20, 20))

    saved = list(tmp_path.glob("*.png"))
    assert len(saved) == 1
    cropped = QPixmap(str(saved[0]))
    assert cropped.width() == 40
    assert cropped.height() == 40
    px = cropped.toImage().pixelColor(5, 5)
    assert px.blue() > 200 and px.red() < 50, f"expected blue pixel, got {px.name()}"

    selector.close()
