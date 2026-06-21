"""Clipboard watcher for Snippy.

Watches `QApplication.clipboard()` for new text content and emits a Qt
signal whenever a *new* (non-duplicate) text snippet is detected.

Features:
- Dedups consecutive identical copies
- Honors the configured blacklist regex (drops copies that look like secrets)
- Honors a max-snippet-length cap
- Trims trailing whitespace and skips purely-whitespace copies
- Self-copy suppression: if `self_source_token` was just set on the
  clipboard by Snippy itself, the next clipboard change is ignored
  (prevents the "I copied a snippet back to the clipboard → re-stored"
  feedback loop).

Phase 1 polls the clipboard every 250 ms (good enough; `QClipboard` has
no native cross-platform change signal that's reliable enough for us).
"""
from __future__ import annotations

import logging
import re
from typing import Iterable

from PySide6.QtCore import QObject, QTimer, Signal

from snippy.core.config import CaptureConfig


LOGGER = logging.getLogger(__name__)

POLL_INTERVAL_MS = 250


class ClipboardWatcher(QObject):
    """Watches the system clipboard and emits `snippet_captured(str)` signals."""

    snippet_captured = Signal(str)  # raw text content

    def __init__(
        self,
        app: QObject,  # the QApplication (used to access the clipboard)
        config: CaptureConfig,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._app = app
        self._config = config
        self._last_text: str = ""
        self._self_source_token: str = ""  # set by mark_self_copy()
        self._blacklist_patterns: list[re.Pattern[str]] = [
            re.compile(p) for p in config.blacklist_regex
        ]
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._tick)

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        # Seed last_text with whatever is on the clipboard right now so we
        # don't re-capture a pre-existing copy on launch.
        try:
            self._last_text = self._app.clipboard().text() or ""
        except Exception:  # pragma: no cover
            self._last_text = ""
        self._timer.start()
        LOGGER.info("ClipboardWatcher started (poll every %d ms)", POLL_INTERVAL_MS)

    def stop(self) -> None:
        self._timer.stop()
        LOGGER.info("ClipboardWatcher stopped")

    def is_running(self) -> bool:
        return self._timer.isActive()

    # -- self-copy suppression -------------------------------------------

    def mark_self_copy(self, text: str) -> None:
        """Call this right before programmatically setting the clipboard.

        The next clipboard-poll tick that sees this exact text will be
        ignored, so we don't end up storing our own pastes.
        """
        self._self_source_token = text

    # -- internals --------------------------------------------------------

    def _tick(self) -> None:
        try:
            text = self._app.clipboard().text() or ""
        except Exception:  # pragma: no cover
            return

        if not text or text == self._last_text:
            return

        self._last_text = text

        if self._self_source_token and text == self._self_source_token:
            LOGGER.debug("Ignored self-copy (loop suppression)")
            self._self_source_token = ""
            return

        cleaned = text.strip()
        if not cleaned:
            return

        if len(cleaned) > self._config.max_snippet_length:
            LOGGER.debug("Skipped over-long snippet (%d chars)", len(cleaned))
            return

        if self._is_blacklisted(cleaned):
            LOGGER.info("Skipped blacklisted snippet (looked like a secret)")
            return

        LOGGER.debug("New clipboard capture: %d chars", len(cleaned))
        self.snippet_captured.emit(cleaned)

    def _is_blacklisted(self, content: str) -> bool:
        return any(p.search(content) for p in self._blacklist_patterns)

    def update_blacklist(self, patterns: Iterable[str]) -> None:
        self._blacklist_patterns = [re.compile(p) for p in patterns]
        self._config.blacklist_regex = list(patterns)