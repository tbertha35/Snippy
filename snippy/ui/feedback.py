"""Capture feedback for Snippy.

Implements the "you should always know Snippy caught it" requirement:
- Toast notification (corner of screen, 1.5s, auto-fade)
- Tray icon flash (color-coded by snippet type)

Phase 1 keeps this simple — single timer-driven toast window, no animation
framework. Phase 2 will replace it with smoother fade animations and a
queue for multiple toasts.
"""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QPropertyAnimation, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtMultimedia import QSoundEffect
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSystemTrayIcon,
    QVBoxLayout,
)

from snippy.core.detector import icon_for
from snippy.core.logging import setup_logging


LOGGER = logging.getLogger(__name__)

TOAST_DURATION_MS = 1500
TOAST_MARGIN = 24
TOAST_WIDTH = 360
TOAST_HEIGHT = 72


# ---------------------------------------------------------------------------
# Toast widget
# ---------------------------------------------------------------------------


class Toast(QFrame):
    """A small frameless card that fades after a few seconds."""

    def __init__(self, title: str, body: str, accent_color: str) -> None:
        super().__init__()
        self.setObjectName("toast")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setFixedSize(TOAST_WIDTH, TOAST_HEIGHT)

        # Accent the left border with the type's color.
        self.setStyleSheet(
            f"QFrame#toast {{ border-left: 4px solid {accent_color}; }}"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        self._icon_label = QLabel("", self)
        self._icon_label.setObjectName("toastIcon")
        self._icon_label.setStyleSheet("font-size: 18px;")

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)

        self._title_label = QLabel(title, self)
        self._title_label.setObjectName("toastTitle")
        self._body_label = QLabel(body, self)
        self._body_label.setObjectName("toastBody")
        self._body_label.setWordWrap(True)
        self._body_label.setMaximumHeight(40)

        text_layout.addWidget(self._title_label)
        text_layout.addWidget(self._body_label)

        layout.addWidget(self._icon_label)
        layout.addLayout(text_layout, stretch=1)

        # Fade-out animation
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(280)
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._begin_fade)

    def set_icon(self, type_: str) -> None:
        self._icon_label.setText(icon_for(type_))

    def position_at(self, bottom_right: QPoint) -> None:
        x = bottom_right.x() - self.width() - TOAST_MARGIN
        y = bottom_right.y() - self.height() - TOAST_MARGIN
        self.move(x, y)

    def show_and_die(self) -> None:
        self.show()
        self.raise_()
        self._hide_timer.start(TOAST_DURATION_MS)

    def _begin_fade(self) -> None:
        self._fade.start()
        self._fade.finished.connect(self.deleteLater)


# ---------------------------------------------------------------------------
# Tray icon (with color flash)
# ---------------------------------------------------------------------------


def _appicon_path() -> Path | None:
    """Return the path to the bundled app icon, or None if not present.

    The icon lives at `snippy/assets/appimage.png` (a 256x256 PNG supplied
    by the user). We probe both the source-tree location (dev) and the
    PyInstaller `_MEIPASS` location (frozen .exe) so the same code path
    works in dev, in the Windows installer, in the macOS .app, and in
    the Linux AppImage / .deb.
    """
    from snippy import assets_dir  # local import; avoids cycle in tests
    candidates = [
        Path(assets_dir()) / "appimage.png",
        Path(assets_dir()) / "icon.png",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def _build_default_icon() -> QIcon:
    """Return the Snippy app icon.

    Prefers the bundled PNG (`snippy/assets/appimage.png`) so the tray,
    the main window, and the .exe all show the real Snippy logo. Falls
    back to a tiny programmatically-drawn scissor icon if the PNG is
    missing (e.g. in a CI smoke test that didn't bundle assets).
    """
    icon_path = _appicon_path()
    if icon_path is not None:
        pix = QPixmap(str(icon_path))
        if not pix.isNull():
            return QIcon(pix)
    # Fallback: tiny programmatically-drawn scissor icon
    pix = QPixmap(64, 64)
    pix.fill(QColor("#0f172a"))
    p = QPainter(pix)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(QColor("#3b82f6"))
        p.setBrush(QColor("#3b82f6"))
        p.drawEllipse(8, 8, 28, 28)
        p.drawEllipse(28, 28, 28, 28)
        p.setPen(QColor("#e2e8f0"))
        p.setBrush(QColor("#e2e8f0"))
        p.drawEllipse(18, 18, 8, 8)
        p.drawEllipse(38, 38, 8, 8)
    finally:
        p.end()
    return QIcon(pix)


def _accent_for_type(type_: str) -> str:
    from snippy.core.detector import TYPE_COLORS, TYPE_TEXT

    return TYPE_COLORS.get(type_, TYPE_COLORS[TYPE_TEXT])


class FeedbackBus(QObject):
    """Coordinates toasts + tray icon feedback."""

    def __init__(self, app: QApplication, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._app = app
        self._tray: QSystemTrayIcon | None = None
        self._base_icon: QIcon | None = None
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._restore_tray_icon)

    def set_tray(self, tray: QSystemTrayIcon) -> None:
        self._tray = tray
        if self._base_icon is None or self._base_icon.isNull():
            self._base_icon = tray.icon()
            if self._base_icon is None or self._base_icon.isNull():
                self._base_icon = _build_default_icon()
                tray.setIcon(self._base_icon)

    def show_capture(self, content: str, content_type: str) -> None:
        """Fire a toast + flash the tray icon (if both are enabled)."""
        # --- Toast ---
        title = "Snippy captured"
        body = content if len(content) <= 80 else content[:77] + "…"
        toast = Toast(title, body, accent_color=_accent_for_type(content_type))
        toast.set_icon(content_type)
        try:
            geom = self._app.primaryScreen().availableGeometry()
            toast.position_at(QPoint(geom.right(), geom.bottom()))
        except Exception:  # pragma: no cover
            pass
        toast.show_and_die()

        # --- Tray flash ---
        if self._tray is not None and self._base_icon is not None:
            self._flash_tray(content_type)

    def _flash_tray(self, content_type: str) -> None:
        if self._tray is None or self._base_icon is None:
            return
        flash_pix = QPixmap(self._base_icon.pixmap(QSize(64, 64)))
        p = QPainter(flash_pix)
        try:
            color = QColor(_accent_for_type(content_type))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
            p.fillRect(flash_pix.rect(), color)
        finally:
            p.end()
        self._tray.setIcon(QIcon(flash_pix))
        self._flash_timer.start(450)

    def _restore_tray_icon(self) -> None:
        if self._tray is not None and self._base_icon is not None:
            self._tray.setIcon(self._base_icon)

    # -- WS9: optional capture sound -------------------------------------

    _sound_effect: QSoundEffect | None = None

    def play_capture_sound(self) -> None:
        """Play a subtle capture 'ding' (WS9). No-op if the WAV file is missing."""
        from pathlib import Path

        # Lazily build the QSoundEffect and reuse it
        if self._sound_effect is None:
            try:
                self._sound_effect = QSoundEffect(self)
            except Exception as exc:  # pragma: no cover
                LOGGER.debug("QSoundEffect unavailable: %s", exc)
                return

        # Look for assets/ding.wav (bundled). If absent, silent no-op.
        try:
            from snippy import __file__ as pkg_init
            pkg_dir = Path(pkg_init).parent
            wav = pkg_dir / "assets" / "ding.wav"
            if not wav.exists():
                LOGGER.debug("Capture sound skipped: %s not found", wav)
                return
            self._sound_effect.setSource(QUrl.fromLocalFile(str(wav)))
            self._sound_effect.setVolume(0.4)  # subtle
            self._sound_effect.play()
        except Exception as exc:  # pragma: no cover (never block capture on sound)
            LOGGER.debug("Capture sound failed: %s", exc)
