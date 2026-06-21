"""Screen-region capture for Snippy.

Provides a Windows Snipping Tool-style experience: a transparent fullscreen
overlay dims the screen and lets the user click-drag a rectangle. The selected
region is grabbed as a `QPixmap`, saved to disk as a PNG, and optionally copied
to the system clipboard.

The saved image is then stored in the Snippy database as a snippet with
`content_type='image'` and `content` equal to the absolute image path.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel, QWidget

try:
    # QScreen is in QtGui on PySide6 6.x; older versions may differ.
    from PySide6.QtGui import QScreen  # type: ignore[attr-defined]
except Exception:  # pragma: no cover
    QScreen = object  # type: ignore[assignment,misc]


try:
    from platformdirs import user_pictures_dir
except Exception:  # pragma: no cover — fallback if platformdirs is older
    user_pictures_dir = None  # type: ignore[assignment]


LOGGER = logging.getLogger(__name__)

# Semi-transparent dim color used behind the selected region.
_DIM_COLOR = QColor(0, 0, 0, 120)
# Selection border color.
_BORDER_COLOR = QColor(255, 255, 255, 220)
# Selection fill color (very subtle).
_FILL_COLOR = QColor(255, 255, 255, 40)


def default_image_save_dir() -> Path:
    """Return the user's Pictures folder, falling back to the home directory."""
    if user_pictures_dir is not None:
        try:
            path = Path(user_pictures_dir())
            path.mkdir(parents=True, exist_ok=True)
            return path
        except Exception:
            pass
    return Path.home() / "Pictures"


def resolve_image_save_dir(configured: str | None) -> Path:
    """Return the configured image save dir or the platform default."""
    if configured:
        path = Path(configured).expanduser()
        if path.is_dir():
            return path
    return default_image_save_dir()


def generate_image_filename(prefix: str = "snippy_capture") -> str:
    """Return a timestamped PNG filename like `snippy_capture_20260619_093012.png`."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{stamp}.png"


def save_captured_image(
    pixmap: QPixmap,
    directory: Path | None = None,
    filename: str | None = None,
) -> Path:
    """Save a captured pixmap to disk and return the absolute path."""
    directory = directory or default_image_save_dir()
    directory.mkdir(parents=True, exist_ok=True)
    filename = filename or generate_image_filename()
    if not filename.lower().endswith(".png"):
        filename += ".png"
    path = (directory / filename).resolve()
    if not pixmap.save(str(path), "PNG"):
        raise RuntimeError(f"Failed to save screenshot to {path}")
    LOGGER.info("Saved screenshot to %s", path)
    return path


def copy_pixmap_to_clipboard(pixmap: QPixmap) -> None:
    """Copy the given pixmap to the system clipboard."""
    app = QApplication.instance()
    if app is None:
        return
    clipboard = app.clipboard()
    if clipboard is None:
        return
    clipboard.setPixmap(pixmap)


def _logical_rect_to_device(rect: QRect, dpr: float) -> QRect:
    """Convert logical (widget) coordinates to the pixmap's device pixels."""
    return QRect(
        int(round(rect.x() * dpr)),
        int(round(rect.y() * dpr)),
        int(round(rect.width() * dpr)),
        int(round(rect.height() * dpr)),
    )


class _ScreenGrab:
    """A single screen's device-pixel grab plus the metadata needed to crop it.

    `pixmap` is the raw device-pixel pixmap returned by `QScreen.grabWindow(0)`.
    `geometry` is the screen's logical geometry (in virtual-desktop coordinates).
    `dpr` is the screen's devicePixelRatio at the time of capture.
    `composite_offset` is where this screen's pixmap was placed in the
    composite, in **composite device pixels**.
    """

    __slots__ = ("pixmap", "geometry", "dpr", "composite_offset")

    def __init__(self, pixmap: QPixmap, geometry: QRect, dpr: float, composite_offset: QPoint) -> None:
        self.pixmap = pixmap
        self.geometry = QRect(geometry)
        self.dpr = float(dpr) if dpr > 0 else 1.0
        self.composite_offset = QPoint(composite_offset)


def _grab_desktop(app: QApplication) -> tuple[QPixmap, list[_ScreenGrab], QRect, QPoint] | None:
    """Capture every connected screen into a single composite pixmap.

    Returns ``(composite, screen_grabs, virtual_geometry, virtual_top_left)``
    or ``None`` if no screens are available.

    - ``composite`` is one pixmap covering the full virtual desktop, with
      `devicePixelRatio` set so Qt renders it at the right size.
    - ``screen_grabs`` lets the caller crop the *original* per-screen pixmap
      using each screen's own devicePixelRatio, which is the only way to be
      correct on mixed-DPI multi-monitor setups.
    - ``virtual_geometry`` is the union of all screen geometries, **normalized
      to start at (0, 0)**, so the overlay widget can be sized with a single
      `setGeometry(rect)` call and still cover every monitor.
    - ``virtual_top_left`` is the offset that must be **added** to mouse
      positions to translate them from widget coords back into the original
      virtual-desktop coordinate space.
    """
    screens = app.screens()
    if not screens:
        return None

    # 1. Virtual geometry: union of all screen geometries, in their original
    #    virtual-desktop coordinates (may have negative origin).
    raw_virtual = QRect()
    for s in screens:
        g = s.geometry()
        if raw_virtual.isNull():
            raw_virtual = QRect(g)
        else:
            raw_virtual = raw_virtual.united(g)
    if raw_virtual.isEmpty():
        return None

    # 2. Per-screen grabs. Use the *pixmap's actual* dimensions (which are
    #    in device pixels for that screen) as the source of truth — never
    #    multiply geometry by dpr, because Qt may have already done that or
    #    rounded it differently.
    grabs: list[_ScreenGrab] = []
    composite_w = 0
    composite_h = 0
    for s in screens:
        pm = s.grabWindow(0)
        if pm.isNull():
            continue
            
        # --- FIX 1: Force raw device pixels by wiping the auto-detected DPR ---
        pm.setDevicePixelRatio(1.0)
        
        g = s.geometry()
        dpr = float(s.devicePixelRatio() or 1.0)
        # Place this screen at its geometry offset within the virtual desktop,
        # translated to (0, 0) for the composite.
        ox = int(round((g.x() - raw_virtual.x()) * dpr))
        oy = int(round((g.y() - raw_virtual.y()) * dpr))
        grabs.append(_ScreenGrab(pm, g, dpr, QPoint(ox, oy)))
        right = ox + pm.width()
        bottom = oy + pm.height()
        if right > composite_w:
            composite_w = right
        if bottom > composite_h:
            composite_h = bottom

    if not grabs or composite_w == 0 or composite_h == 0:
        return None

    # 3. Composite pixmap: copy each per-screen grab into place. The composite
    #    itself stays a pure device-pixel pixmap (no setDevicePixelRatio yet)
    #    so the dim overlay paint and QPixmap.copy(rect) work in device
    #    pixels consistently.
    composite = QPixmap(composite_w, composite_h)
    composite.fill(Qt.GlobalColor.black)
    painter = QPainter(composite)
    try:
        for g in grabs:
            painter.drawPixmap(g.composite_offset, g.pixmap)
    finally:
        painter.end()

    # 4. Normalized virtual geometry for the overlay (always starts at 0,0).
    virtual_for_widget = QRect(0, 0, raw_virtual.width(), raw_virtual.height())

    return composite, grabs, virtual_for_widget, QPoint(raw_virtual.x(), raw_virtual.y())


class RegionSelector(QWidget):
    """Fullscreen transparent overlay for click-drag region selection.

    Emits `region_selected(QRect)` when the user releases the mouse after
    dragging a rectangle. Pressing Escape or clicking without dragging cancels.
    """

    region_selected = Signal(QRect)
    cancelled = Signal()

    def __init__(
        self,
        screen_pixmap: QPixmap,
        *,
        device_pixel_ratio: float = 1.0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        # The composite is a pure *device-pixel* pixmap. We must NOT call
        # setDevicePixelRatio on it, because then QPixmap.copy(rect) would
        # interpret the rect in *logical* pixels (the dpr is set on the
        # pixmap) and return the wrong area.
        self._screen_pixmap = screen_pixmap
        # The composite's dpr, used by the caller to translate the user's
        # selection rect (in logical widget coords) to device pixels for
        # cropping.
        self._device_pixel_ratio = float(device_pixel_ratio) if device_pixel_ratio > 0 else 1.0
        self._start: QPoint | None = None
        self._current: QPoint | None = None
        self._set_fullscreen()

    def _set_fullscreen(self) -> None:
        # Cover the entire virtual desktop across all screens. We avoid
        # `showFullScreen()` here because on Windows it can leave the
        # widget in a transient "snapped to the primary monitor at OS
        # default DPI" state, producing a tiny first paint. The caller
        # (`capture_screen_region`) is responsible for sizing us to the
        # normalized virtual geometry *after* construction.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        # Don't show fullscreen; the caller will setGeometry then show().
        self.show()

    def paintEvent(self, event) -> None:  # noqa: N802 — Qt naming convention
        painter = QPainter(self)
        # The composite pixmap is in raw device pixels. The widget itself
        # lives in *logical* coordinates (e.g. 1920×1080 on a 2× display),
        # so a naive `drawPixmap(self.rect(), pixmap, pixmap.rect())` would
        # render only the top-left logical quarter of the device pixmap.
        # Scale the painter by 1/dpr so device pixels line up with logical
        # widget pixels when drawing the composite.
        if self._device_pixel_ratio != 1.0:
            painter.scale(1.0 / self._device_pixel_ratio, 1.0 / self._device_pixel_ratio)
        if self._screen_pixmap.width() > 0 and self._screen_pixmap.height() > 0:
            painter.drawPixmap(0, 0, self._screen_pixmap)
        # Reset the transform so the dim overlay and selection rect use
        # logical widget coords.
        if self._device_pixel_ratio != 1.0:
            painter.resetTransform()
        painter.fillRect(self.rect(), _DIM_COLOR)

        # Draw the selected region bright if we have a drag in progress.
        rect = self._selection_rect()
        if rect is not None and rect.width() > 0 and rect.height() > 0:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.fillRect(rect, _FILL_COLOR)
            painter.setPen(_BORDER_COLOR)
            painter.drawRect(rect)
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._start = event.pos()
            self._current = event.pos()
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._current = event.pos()
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        rect = self._selection_rect()
        self.close()
        if rect is None or rect.width() < 2 or rect.height() < 2:
            self.cancelled.emit()
        else:
            self.region_selected.emit(rect)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            self.cancelled.emit()
        else:
            super().keyPressEvent(event)

    def _selection_rect(self) -> QRect | None:
        if self._start is None or self._current is None:
            return None
        return QRect(self._start, self._current).normalized()


def prompt_for_save_directory(parent: QWidget | None, current: Path) -> Path | None:
    """Open a directory picker for the screenshot save folder."""
    chosen = QFileDialog.getExistingDirectory(
        parent,
        "Choose screenshot save folder",
        str(current),
    )
    if not chosen:
        return None
    return Path(chosen)


def capture_screen_region(
    parent: QWidget | None = None,
    save_dir: Path | None = None,
    copy_to_clipboard: bool = True,
    finished_callback: Callable[[Path | None], None] | None = None,
) -> RegionSelector | None:
    """Start an interactive fullscreen region capture.

    The returned `RegionSelector` is already shown fullscreen. Connect to its
    `region_selected`/`cancelled` signals, or pass a `finished_callback(path)`
    for fire-and-forget usage. `path` is the saved PNG path, or `None` if the
    user cancelled.
    """
    app = QApplication.instance()
    if app is None:
        return None

    grabbed = _grab_desktop(app)
    if grabbed is None:
        return None
    composite, screen_grabs, virtual_geometry, virtual_top_left = grabbed

    # Composite dpr = composite device width / virtual logical width.
    # If a single monitor, this is just that screen's dpr.
    if virtual_geometry.width() > 0 and virtual_geometry.height() > 0:
        dpr_w = composite.width() / virtual_geometry.width()
        dpr_h = composite.height() / virtual_geometry.height()
        composite_dpr = max(dpr_w, dpr_h)
    else:
        composite_dpr = 1.0

    selector = RegionSelector(
        composite, device_pixel_ratio=composite_dpr, parent=parent,
    )
    # The widget uses **logical** coords (matching the virtual geometry).
    # The composite pixmap is `composite_dpr` times larger in device pixels;
    # `paintEvent` stretches it to fill the widget's logical rect, so
    # `event.pos()` and the selection rect come back in logical coords,
    # which is what we want for the user's mental model.
    selector.setGeometry(virtual_geometry)
    # Force an immediate paint at the correct size so the user never sees
    # a transient "tiny top-left" frame on Windows.
    selector.show()
    selector.repaint()
    selector.raise_()
    selector.activateWindow()

    def _crop(widget_rect: QRect) -> QPixmap:
        """Crop the original per-screen grabs using each screen's own DPR.

        `widget_rect` is in the overlay widget's **logical** coordinate
        system (matches the normalized virtual geometry). The widget's
        `devicePixelRatio` is *not* set, so `event.pos()` returns logical
        pixels — the same units the user sees on screen.
        """
        if not screen_grabs:
            return QPixmap()

        # Translate from normalized-virtual coords (widget) to original
        # virtual-desktop coords (which may have a negative origin).
        desktop_rect = QRect(
            widget_rect.x() + virtual_top_left.x(),
            widget_rect.y() + virtual_top_left.y(),
            widget_rect.width(),
            widget_rect.height(),
        )

        # Fast path: the selection lies entirely within one screen. Crop
        # that screen's pixmap directly using its own dpr — no composite
        # slicing, no scaling mismatches.
        for sg in screen_grabs:
            if sg.geometry.contains(desktop_rect):
                local = QRect(
                    desktop_rect.x() - sg.geometry.x(),
                    desktop_rect.y() - sg.geometry.y(),
                    desktop_rect.width(),
                    desktop_rect.height(),
                )
                local_device = _logical_rect_to_device(local, sg.dpr)
                local_device = local_device.intersected(sg.pixmap.rect())
                if local_device.isEmpty():
                    return QPixmap()
                return sg.pixmap.copy(local_device)

        # Slow path: the selection spans multiple screens. Composite a
        # small pixmap covering just the requested region by copying the
        # relevant slice of each contributing screen.
        out_w = int(round(desktop_rect.width() * max(sg.dpr for sg in screen_grabs)))
        out_h = int(round(desktop_rect.height() * max(sg.dpr for sg in screen_grabs)))
        if out_w <= 0 or out_h <= 0:
            return QPixmap()
        out = QPixmap(out_w, out_h)
        out.fill(Qt.GlobalColor.black)
        painter = QPainter(out)
        try:
            for sg in screen_grabs:
                # Intersection of the requested rect with this screen, in
                # the screen's logical coords.
                inter = desktop_rect.intersected(sg.geometry)
                if inter.isEmpty():
                    continue
                local = QRect(
                    inter.x() - sg.geometry.x(),
                    inter.y() - sg.geometry.y(),
                    inter.width(),
                    inter.height(),
                )
                local_device = local.adjusted(0, 0, 0, 0)
                local_device = QRect(
                    int(round(local.x() * sg.dpr)),
                    int(round(local.y() * sg.dpr)),
                    int(round(local.width() * sg.dpr)),
                    int(round(local.height() * sg.dpr)),
                )
                slice_pm = sg.pixmap.copy(local_device)
                # Place into out at the slice's offset, in *out* coords
                # (using the highest dpr as the canonical out scale).
                max_dpr = max(s.dpr for s in screen_grabs)
                dx = int(round((inter.x() - desktop_rect.x()) * max_dpr))
                dy = int(round((inter.y() - desktop_rect.y()) * max_dpr))
                painter.drawPixmap(dx, dy, slice_pm)
        finally:
            painter.end()
        return out

    def _on_selected(rect: QRect) -> None:
        cropped = _crop(rect)
        if cropped.isNull() or cropped.width() < 2 or cropped.height() < 2:
            if finished_callback:
                finished_callback(None)
            return
        directory = save_dir or default_image_save_dir()
        try:
            path = save_captured_image(cropped, directory=directory)
        except Exception as exc:
            LOGGER.warning("Failed to save screenshot: %s", exc)
            if finished_callback:
                finished_callback(None)
            return
        if copy_to_clipboard:
            copy_pixmap_to_clipboard(cropped)
        if finished_callback:
            finished_callback(path)

    selector.region_selected.connect(_on_selected)
    selector.cancelled.connect(lambda: finished_callback(None) if finished_callback else None)
    selector.show()
    selector.raise_()
    selector.activateWindow()
    return selector


class ImageViewer(QLabel):
    """Simple resizable window that displays a QPixmap at its natural size."""

    def __init__(self, pixmap: QPixmap, title: str = "Snippy — Image", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowTitleHint
        )
        
        # --- FIX 2: Apply the screen's DPI scale to the preview image ---
        screen = QApplication.primaryScreen()
        if screen is not None:
            pixmap.setDevicePixelRatio(screen.devicePixelRatio())

        self.setPixmap(pixmap)
        self.setScaledContents(False)
        self.adjustSize()
        self.setMinimumSize(200, 150)
        
        # Center on screen.
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(geo.center() - self.rect().center())