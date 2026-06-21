"""History window for Snippy \u2014 Phase 2 WS2.

A separate `QMainWindow` showing *all* (non-archived) snippets in a sortable,
multi-selectable `QTableView`. The user can:
- Free-text search (same operators as the global search, via the shared `core.search`)
- Filter by type via clickable chips
- Right-click for context (copy, open URL, pin, archive, delete)
- Select multiple rows + bulk delete / archive / tag (WS5)

The window is intentionally modeless \u2014 opening it doesn't dismiss the
the main Snippy window.
"""
from __future__ import annotations

import logging
import sys
from typing import Callable

from PySide6.QtCore import QEvent, QPoint, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from snippy.core.db import Snippet


LOGGER = logging.getLogger(__name__)


# Roles we attach to each row of the table
_ROLE_SNIPPET = Qt.ItemDataRole.UserRole + 1


# SnippetTableModel is a small QAbstractTableModel that renders a list of
# Snippet objects. We keep it inline because it's only used here and has
# no Phase 2+ value in being a separate file.
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QTimer


class SnippetTableModel(QAbstractTableModel):
    COLUMNS = ("\u2b50", "Type", "Preview", "Uses", "Last used", "Created")

    # Thumbnail size for image rows in the Preview column.
    THUMB_SIZE = 64

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[Snippet] = []

    def set_rows(self, rows: list[Snippet]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self.COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.COLUMNS):
            return self.COLUMNS[section]
        if orientation == Qt.Orientation.Vertical:
            return section + 1
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        snip = self._rows[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return "\u2605" if snip.is_pinned else ""
            if col == 1:
                return snip.content_type
            if col == 2:
                if snip.content_type == "image":
                    return snip.content
                first = snip.content.splitlines()[0] if snip.content else ""
                return (first[:80] + "\u2026") if len(first) > 80 else first
            if col == 3:
                return str(snip.use_count)
            if col == 4:
                return snip.last_used_at or ""
            if col == 5:
                return snip.created_at or ""
        elif role == Qt.ItemDataRole.DecorationRole:
            if col == 2 and snip.content_type == "image":
                px = self._load_thumbnail(snip.content)
                if px is not None:
                    return QIcon(px)
        elif role == _ROLE_SNIPPET:
            return snip
        elif role == Qt.ItemDataRole.ToolTipRole:
            if snip.content_type == "image":
                return f"Image: {snip.content}"
            return snip.content
        return None

    def _load_thumbnail(self, image_path: str) -> QPixmap | None:
        from pathlib import Path

        path = Path(image_path)
        if not path.is_file():
            return None
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            return None
        return pixmap.scaled(
            self.THUMB_SIZE,
            self.THUMB_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def snippet_at(self, row: int) -> Snippet | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None


# ---------------------------------------------------------------------------
# The history window
# ---------------------------------------------------------------------------


class HistoryWindow(QDialog):
    """Browser-style list of all snippets. Modal-free, is the main Snippy window."""

    snippet_activated = Signal(Snippet)  # user wants to copy a row
    snippet_details_requested = Signal(Snippet)  # WS4: open detail dialog
    snippets_deleted = Signal(list)       # list[Snippet] (WS5 bulk)
    snippets_archived = Signal(list)     # list[Snippet]
    snippets_unarchived = Signal(list)   # v0.3.x: list[Snippet]
    snippets_pinned = Signal(list, bool) # list[Snippet], pinned=True/False
    snippets_reordered = Signal(list)  # list[Snippet] in new pinned order
    tags_requested = Signal(list)         # WS6: add tags to N snippets, list[str]
    tags_remove_requested = Signal(list, str)  # WS6: remove one tag, list[Snippet]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("history")
        # Use a normal top-level window so the history gets a title bar,
        # a taskbar button, and can be moved/resized across monitors.
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowTitleHint
        )
        self.setWindowTitle("Snippy \u2014 History")
        self.resize(960, 540)
        # Don't let closing the history window quit the whole app.
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self._all_provider: Callable[[], list[Snippet]] = lambda: []
        # Default confirm dialog (overridable via set_confirm_callback)
        self._confirm_callback = self._default_confirm
        self._build_ui()
        # Debounce search input so we don't re-search on every keystroke
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(120)
        self._search_timer.timeout.connect(self._refresh)

        # Global shortcuts that work even while the search box has focus.
        self._sc_activate = QShortcut(QKeySequence("Return"), self)
        self._sc_activate.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._sc_activate.activated.connect(self._activate_selected_row)
        self._sc_copy2 = QShortcut(QKeySequence("Enter"), self)
        self._sc_copy2.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._sc_copy2.activated.connect(self._activate_selected_row)

        self._sc_pin = QShortcut(QKeySequence("Ctrl+P"), self)
        self._sc_pin.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._sc_pin.activated.connect(self._toggle_pin_selected)

        # Arrow keys navigate the table even when the search box has focus.
        self._sc_up = QShortcut(QKeySequence("Up"), self)
        self._sc_up.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._sc_up.activated.connect(self._move_selection)
        self._sc_down = QShortcut(QKeySequence("Down"), self)
        self._sc_down.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._sc_down.activated.connect(self._move_selection)
        self._sc_pgup = QShortcut(QKeySequence("PgUp"), self)
        self._sc_pgup.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._sc_pgup.activated.connect(self._move_selection)
        self._sc_pgdown = QShortcut(QKeySequence("PgDown"), self)
        self._sc_pgdown.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._sc_pgdown.activated.connect(self._move_selection)

    def _activate_selected_row(self) -> None:
        """Copy the currently selected snippet to the clipboard."""
        snips = self._selected_snippets()
        if not snips:
            return
        self.snippet_activated.emit(snips[0])

    def _toggle_pin_selected(self) -> None:
        """Toggle pin on the currently selected snippet(s)."""
        snips = self._selected_snippets()
        if not snips:
            return
        new_state = not snips[0].is_pinned
        self.snippets_pinned.emit(snips, new_state)

    def _move_selection(self) -> None:
        """Move the table selection up/down one row (or page)."""
        shortcut = self.sender()
        if shortcut is self._sc_up:
            delta = -1
        elif shortcut is self._sc_down:
            delta = 1
        elif shortcut is self._sc_pgup:
            delta = -10
        elif shortcut is self._sc_pgdown:
            delta = 10
        else:
            return

        model = self._table.selectionModel()
        current = model.currentIndex()
        if not current.isValid():
            # No current row: select the first or last visible row.
            row = 0 if delta > 0 else max(0, self._proxy.rowCount() - 1)
        else:
            row = current.row() + delta
        row = max(0, min(row, self._proxy.rowCount() - 1))
        self._table.selectRow(row)
        self._table.scrollTo(self._proxy.index(row, 0))

    def set_snippet_provider(self, provider: Callable[[], list[Snippet]]) -> None:
        """Provider for active (non-archived) snippets."""
        self._all_provider = provider
        self._refresh()

    def set_archived_provider(self, provider: Callable[[], list[Snippet]]) -> None:
        """Provider for archived snippets. Used when the 'Archived' chip
        is toggled on. Should return all rows where `is_archived` is True.
        """
        self._archived_provider: Callable[[], list[Snippet]] = provider
        self._refresh()

    def show_and_focus(self) -> None:
        self._refresh()
        self.show()
        self.raise_()
        self.activateWindow()
        # Force the window to the front on macOS; setActiveWindow helps when
        # LSUIElement keeps the app from becoming the foreground app.
        app = QApplication.instance()
        if app is not None:
            app.setActiveWindow(self)
        self._search.setFocus()

    # -- UI build --------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # Top: search + chips
        top = QHBoxLayout()
        top.setSpacing(6)
        self._search = QLineEdit(self)
        self._search.setObjectName("history_search")
        self._search.setPlaceholderText(
            "Filter \u2026 (try tag:work type:url pin:yes before:2026-01-01)"
        )
        self._search.textChanged.connect(lambda _: self._search_timer.start())
        # Enter in the search box copies the currently selected row.
        self._search.returnPressed.connect(self._activate_selected_row)
        top.addWidget(self._search, stretch=1)

        # Type-filter chips — v0.3.x: mutually exclusive single-select
        # with an explicit "All" option. Previous design used independent
        # checkable buttons; combined with a midnight theme that had no
        # :checked style, the user couldn't see which (if any) chip was
        # active. A QButtonGroup with autoExclusive gives us unambiguous
        # "exactly one selected at a time" semantics.
        self._chip_group = QButtonGroup(self)
        self._chip_group.setExclusive(True)

        # The types we offer as filter chips. `None` is the "All" sentinel.
        chip_specs: list[tuple[str | None, str, str]] = [
            (None,       "\u2728", "All"),
            ("text",     "\U0001f4dd", "Text"),
            ("url",      "\U0001f517", "URL"),
            ("email",    "\u2709",     "Email"),
            ("code",     "\U0001f4bb", "Code"),
            ("path",     "\U0001f4c1", "Path"),
            ("image",    "\U0001f5bc", "Image"),
        ]
        for type_value, icon, label in chip_specs:
            btn = QPushButton(f"{icon} {label}", self)
            btn.setCheckable(True)
            obj_name = f"chip_{type_value or 'all'}"
            btn.setObjectName(obj_name)
            tip = (
                "Show all snippet types"
                if type_value is None
                else f"Show only {type_value} snippets"
            )
            btn.setToolTip(tip)
            # Default to "All" selected.
            if type_value is None:
                btn.setChecked(True)
            self._chip_group.addButton(btn)
            btn.toggled.connect(lambda _checked, _t=type_value: self._on_chip_toggled(_t, _checked))
            top.addWidget(btn)
            setattr(self, f"_chip_{type_value or 'all'}", btn)

        # "Show archived" toggle (v0.3.x). When checked, the table shows
        # archived snippets instead of active ones. The bulk actions
        # automatically switch to "Unarchive" in this mode.
        self._chip_archived = QPushButton("\U0001f4e6 Archived", self)
        self._chip_archived.setObjectName("chip_archived")
        self._chip_archived.setCheckable(True)
        self._chip_archived.setToolTip(
            "Show archived snippets instead of active ones.\n"
            "Archived snippets are hidden from search,\n"
            "but stay in the database and can be restored from here."
        )
        self._chip_archived.toggled.connect(self._on_archived_toggled)
        top.addWidget(self._chip_archived)

        # Clear filters button
        clear_btn = QPushButton("\u2715 Clear filters", self)
        clear_btn.setObjectName("chip_clear")
        clear_btn.setToolTip("Reset the search field and select the 'All' type chip")
        clear_btn.clicked.connect(self._on_clear_filters)
        top.addWidget(clear_btn)

        layout.addLayout(top)

        # Table
        self._model = SnippetTableModel(self)
        self._proxy = QSortFilterProxyModel(self)
        self._proxy.setSourceModel(self._model)
        self._proxy.setSortRole(Qt.ItemDataRole.DisplayRole)

        self._table = QTableView(self)
        self._table.setObjectName("history_table")
        self._table.setModel(self._proxy)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(False)  # disable while dragging; re-enable after if desired
        self._table.setDragEnabled(True)
        self._table.setAcceptDrops(True)
        self._table.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._table.setDropIndicatorShown(True)
        # Intercept the actual drop on the table (the table consumes the event,
        # so HistoryWindow.dropEvent() is never called for internal moves).
        self._table_viewport = self._table.viewport()
        self._table_viewport.installEventFilter(self)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.doubleClicked.connect(self._on_row_double_clicked)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self._table, stretch=1)

        # Footer hint
        footer = QLabel(
            "\u2191/\u2193 navigate \u00b7 \u21b5 copy \u00b7 Ctrl+P pin \u00b7 "
            "Del delete",
            self,
        )
        footer.setObjectName("meta")
        footer.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addWidget(footer)

        # Footer: status + bulk action bar (WS5)
        bottom = QHBoxLayout()
        self._status_label = QLabel("0 snippets", self)
        self._status_label.setObjectName("status")
        bottom.addWidget(self._status_label)
        bottom.addStretch(1)
        # Pin / Unpin are always shown. Archive is shown when viewing
        # active snippets; Unarchive is shown when viewing archived ones.
        # We swap the visible one in _on_archived_toggled.
        for label, slot in [
            ("\U0001f4cc Pin", self._on_bulk_pin),
            ("\U0001f4cc Unpin", self._on_bulk_unpin),
        ]:
            btn = QPushButton(label, self)
            btn.clicked.connect(slot)
            bottom.addWidget(btn)
        self._btn_archive = QPushButton("\U0001f4e5 Archive", self)
        self._btn_archive.setToolTip(
            "Hide the selected snippets from search.\n"
            "They stay in the database and can be restored from\n"
            "History → Archived → Unarchive."
        )
        self._btn_archive.clicked.connect(self._on_bulk_archive)
        bottom.addWidget(self._btn_archive)
        self._btn_unarchive = QPushButton("\U0001f4e4 Unarchive", self)
        self._btn_unarchive.setToolTip(
            "Restore the selected archived snippets to your active library."
        )
        self._btn_unarchive.clicked.connect(self._on_bulk_unarchive)
        self._btn_unarchive.setVisible(False)  # only shown in Archived view
        bottom.addWidget(self._btn_unarchive)
        btn = QPushButton("\U0001f5d1\ufe0f Delete\u2026", self)
        btn.clicked.connect(self._on_bulk_delete)
        bottom.addWidget(btn)
        layout.addLayout(bottom)

    # -- behavior --------------------------------------------------------

    def _showing_archived(self) -> bool:
        return (
            getattr(self, "_chip_archived", None) is not None
            and self._chip_archived.isChecked()
        )

    def _refresh(self) -> None:
        # Pull from the active OR archived provider depending on the toggle.
        if self._showing_archived():
            all_snips = self._archived_provider() if getattr(self, "_archived_provider", None) else []
        else:
            all_snips = self._all_provider()
        # Apply text search + operators
        from snippy.core.search import search as search_engine
        query = self._search.text().strip()
        hits = search_engine(query, all_snips, limit=10_000)
        # Apply type-chip filter (single-select). `_active_type` is None
        # when "All" is selected.
        active_type = self._active_type()
        if active_type is not None:
            hits = [h for h in hits if h.snippet.content_type == active_type]

        # Preserve the user's current sort column/order so live refreshes
        # don't reset the view. QSortFilterProxyModel forgets sort state when
        # the source model is reset, so we save/restore it.
        header = self._table.horizontalHeader()
        sort_col = header.sortIndicatorSection()
        sort_order = header.sortIndicatorOrder()

        # Preserve selection by snippet id so a refresh doesn't jump around.
        selected_ids = {s.id for s in self._selected_snippets()}

        self._model.set_rows([h.snippet for h in hits])

        if sort_col >= 0:
            self._table.sortByColumn(sort_col, sort_order)

        if selected_ids:
            for row in range(self._proxy.rowCount()):
                src = self._proxy.mapToSource(self._proxy.index(row, 0))
                snip = self._model.snippet_at(src.row())
                if snip is not None and snip.id in selected_ids:
                    self._table.selectRow(row)
                    break
        elif hits:
            self._table.selectRow(0)

        self._status_label.setText(f"{len(hits):,} snippet(s)")

    def _selected_snippets(self) -> list[Snippet]:
        snippets: list[Snippet] = []
        for idx in self._table.selectionModel().selectedRows():
            src = self._proxy.mapToSource(idx)
            snip = self._model.snippet_at(src.row())
            if snip is not None:
                snippets.append(snip)
        return snippets

    def _on_row_double_clicked(self, proxy_idx: QModelIndex) -> None:
        src = self._proxy.mapToSource(proxy_idx)
        snip = self._model.snippet_at(src.row())
        if snip is None:
            return
        if snip.content_type == "image":
            self._view_image(snip)
        else:
            self.snippet_activated.emit(snip)

    def eventFilter(self, obj: object, event: object) -> bool:  # noqa: N802 (Qt API)
        """Intercept drops on the table viewport to implement pin reordering."""
        try:
            if obj is self._table_viewport and event.type() == QEvent.Type.Drop:
                self._on_table_drop(event)
                return True
        except Exception:
            # Swallow rare errors from stale/deleted events during shutdown.
            pass
        return super().eventFilter(obj, event)

    def _on_table_drop(self, event: object) -> None:
        """Handle internal row moves to reorder pinned snippets.

        Only pinned rows can be reordered. We compute the new order from
        the drop position, update the database, and re-render.
        """
        if not (event.source() is self._table and event.dropAction() == Qt.DropAction.MoveAction):
            event.ignore()
            return

        # Find the row being dragged.
        selected = self._table.selectionModel().selectedRows()
        if not selected:
            event.ignore()
            return
        src_proxy = selected[0]
        src_idx = self._proxy.mapToSource(src_proxy).row()
        src_snip = self._model.snippet_at(src_idx)
        if src_snip is None or not src_snip.is_pinned:
            event.ignore()
            return

        # Determine destination row from the drop position.
        dst_proxy = self._table.indexAt(event.position().toPoint())
        if not dst_proxy.isValid():
            event.ignore()
            return
        dst_idx = self._proxy.mapToSource(dst_proxy).row()
        dst_snip = self._model.snippet_at(dst_idx)
        if dst_snip is None or not dst_snip.is_pinned:
            event.ignore()
            return

        # Build the ordered list of pinned snippet rows in the source model.
        pinned_rows: list[Snippet] = []
        for i in range(self._model.rowCount()):
            snip = self._model.snippet_at(i)
            if snip is not None and snip.is_pinned:
                pinned_rows.append(snip)
        if src_idx < 0 or src_idx >= len(pinned_rows) or dst_idx < 0 or dst_idx >= len(pinned_rows):
            event.ignore()
            return

        # Move src_snip to the destination position.
        reordered = list(pinned_rows)
        moving = reordered.pop(src_idx)
        insert_at = dst_idx if dst_idx < src_idx else dst_idx
        reordered.insert(insert_at, moving)

        self.snippets_reordered.emit(reordered)
        event.acceptProposedAction()
        self._refresh()

    def _view_image(self, snip: Snippet) -> None:
        """Open a simple viewer for an image snippet."""
        from pathlib import Path
        from snippy.core.capture_screen import ImageViewer

        path = Path(snip.content)
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            QMessageBox.warning(self, "Image unavailable", f"Could not load image:\n{path}")
            return
        viewer = ImageViewer(pixmap, title=f"Snippy — Image #{snip.id}", parent=self)
        viewer.show()
        viewer.raise_()
        viewer.activateWindow()

    def _save_image_as(self, snip: Snippet) -> None:
        """Prompt for a destination and copy the image snippet there."""
        from pathlib import Path

        source = Path(snip.content)
        default = source.name
        dest, _ = QFileDialog.getSaveFileName(
            self,
            "Save image as",
            str(default),
            "Images (*.png *.jpg *.jpeg *.bmp);;All files (*)",
        )
        if not dest:
            return
        try:
            from shutil import copyfile
            copyfile(source, dest)
            LOGGER.info("Saved image #%d to %s", snip.id, dest)
        except Exception as exc:
            QMessageBox.warning(self, "Save failed", f"Could not save image:\n{exc}")

    def _on_context_menu(self, pos: QPoint) -> None:
        snips = self._selected_snippets()
        if not snips:
            return
        menu = QMenu(self)
        snip = snips[0]
        if snip.content_type == "image":
            act_view = QAction("View image\u2026", self)
            act_view.triggered.connect(lambda: self._view_image(snip))
            menu.addAction(act_view)
            act_copy = QAction("Copy image to clipboard", self)
            act_copy.triggered.connect(lambda: self.snippet_activated.emit(snip))
            menu.addAction(act_copy)
            act_save = QAction("Save image as\u2026", self)
            act_save.triggered.connect(lambda: self._save_image_as(snip))
            menu.addAction(act_save)
        else:
            act_copy = QAction("Copy to clipboard", self)
            act_copy.triggered.connect(lambda: self.snippet_activated.emit(snip))
            menu.addAction(act_copy)
            if snip.content_type == "url":
                act_open = QAction("Open URL in browser", self)
                act_open.triggered.connect(lambda: QDesktopServices.openUrl(snip.content))
                menu.addAction(act_open)
            act_details = QAction("Details\u2026  (Ctrl+D)", self)
            act_details.triggered.connect(lambda: self.snippet_details_requested.emit(snip))
            menu.addAction(act_details)
        menu.addSeparator()
        pin_label = "Unpin" if snips[0].is_pinned else "Pin"
        act_pin = QAction(pin_label, self)
        act_pin.triggered.connect(lambda: self.snippets_pinned.emit(snips, not snips[0].is_pinned))
        menu.addAction(act_pin)
        # v0.3.x: show the right "archival" action depending on the current
        # view. In the active view it's "Archive", in the archived view
        # it's "Unarchive". The action label and signal differ.
        if self._showing_archived():
            act_unarchive = QAction("Unarchive (restore to library)", self)
            act_unarchive.triggered.connect(lambda: self.snippets_unarchived.emit(snips))
            menu.addAction(act_unarchive)
        else:
            act_archive = QAction("Archive", self)
            act_archive.setToolTip(
                "Hide from search. Restorable from\n"
                "History → Archived → Unarchive."
            )
            act_archive.triggered.connect(lambda: self.snippets_archived.emit(snips))
            menu.addAction(act_archive)
        menu.addSeparator()
        # WS6: tag submenu — built from the currently-known tags
        tag_menu = menu.addMenu("Tags")
        for tag_name in self._known_tag_names():
            act_add = tag_menu.addAction(f"+ {tag_name}")
            act_add.triggered.connect(lambda _checked=False, t=tag_name: self.tags_requested.emit([t]))
            act_rm = tag_menu.addAction(f"\u2212 {tag_name}")
            act_rm.triggered.connect(lambda _checked=False, t=tag_name: self.tags_remove_requested.emit(snips, t))
        act_new_tag = tag_menu.addAction("New tag\u2026")
        act_new_tag.triggered.connect(lambda: self._prompt_new_tag(snips))
        menu.addSeparator()
        act_delete = QAction("Delete\u2026", self)
        act_delete.triggered.connect(lambda: self._confirm_and_delete(snips))
        menu.addAction(act_delete)
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _confirm_and_delete(self, snips: list[Snippet]) -> None:
        if not snips:
            return
        if len(snips) == 1:
            prompt = f"Delete snippet #{snips[0].id}?"
        else:
            prompt = f"Delete {len(snips)} snippets?"
        if not self._confirm_callback(prompt):
            return
        self.snippets_deleted.emit(snips)

    def set_confirm_callback(self, fn) -> None:
        """Override the confirm dialog (default uses QMessageBox.question).
        Tests pass a stub to avoid blocking on the modal in offscreen mode.
        """
        self._confirm_callback = fn

    # Default confirm callback (QMessageBox). Overridden via set_confirm_callback.
    def _default_confirm(self, prompt: str) -> bool:
        reply = QMessageBox.question(
            self, "Delete", prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    # -- WS6 tag hooks --------------------------------------------------

    def set_tags_provider(self, provider: Callable[[], dict[int, set[str]]]) -> None:
        """Provider returning {snippet_id: {tag, ...}} (re-reads the current state)."""
        self._tags_provider: Callable[[], dict[int, set[str]]] = provider

    def set_tag_lister(self, provider: Callable[[], list[str]]) -> None:
        """Provider returning the list of known tag names (for the right-click menu)."""
        self._tag_lister: Callable[[], list[str]] = provider

    def _known_tag_names(self) -> list[str]:
        try:
            return list(self._tag_lister() or [])
        except Exception:
            return []

    def _prompt_new_tag(self, snips: list[Snippet]) -> None:
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(
            self, "Add tag", "Tag name (lowercased, trimmed):",
        )
        if not ok or not name.strip():
            return
        # Emit so the host (app.py) can persist via db.add_tag + set_tags
        # We piggy-back on tags_requested: list[str] of tags to ADD
        # Caller is expected to merge with existing tags.
        self.tags_requested.emit([name.strip()])

    # -- v0.3.x: type filter chip helpers --------------------------------

    def _active_type(self) -> str | None:
        """Return the type of the currently-checked chip, or None for 'All'."""
        checked = self._chip_group.checkedButton()
        if checked is None:
            # Nothing selected (shouldn't happen because "All" is default);
            # behave as "All" so the user always sees something.
            return None
        obj_name = checked.objectName() or ""
        # objectName is "chip_<type>" or "chip_all"
        if obj_name == "chip_all":
            return None
        if obj_name.startswith("chip_"):
            return obj_name[len("chip_"):]
        return None

    def _on_chip_toggled(self, type_value: str | None, checked: bool) -> None:
        """When a chip is checked, refresh the table. The button group's
        exclusive setting handles the uncheck for the previously-selected
        chip automatically; we only care about the new selection.
        """
        if not checked:
            return
        LOGGER.debug("History chip toggled: type=%r checked=True", type_value)
        self._search_timer.start()

    def _on_clear_filters(self) -> None:
        """Reset the search field and the type chip back to 'All'."""
        self._search.clear()
        all_btn = getattr(self, "_chip_all", None)
        if all_btn is not None and not all_btn.isChecked():
            all_btn.setChecked(True)
        # The chip toggled signal will trigger _refresh via the timer
        self._search_timer.start()

    def _on_archived_toggled(self, checked: bool) -> None:
        """Swap Archive<->Unarchive bulk button and refresh the table."""
        if hasattr(self, "_btn_archive") and hasattr(self, "_btn_unarchive"):
            self._btn_archive.setVisible(not checked)
            self._btn_unarchive.setVisible(checked)
        # Update the window title to reflect the current view
        self.setWindowTitle(
            "Snippy — History (Archived)" if checked else "Snippy — History"
        )
        self._search_timer.start()

    # -- bulk actions (WS5) ---------------------------------------------

    def _on_bulk_pin(self) -> None:
        snips = self._selected_snippets()
        if snips:
            self.snippets_pinned.emit(snips, True)

    def _on_bulk_unpin(self) -> None:
        snips = self._selected_snippets()
        if snips:
            self.snippets_pinned.emit(snips, False)

    def _on_bulk_archive(self) -> None:
        snips = self._selected_snippets()
        if snips:
            self.snippets_archived.emit(snips)

    def _on_bulk_unarchive(self) -> None:
        snips = self._selected_snippets()
        if snips:
            self.snippets_unarchived.emit(snips)

    def _on_bulk_delete(self) -> None:
        self._confirm_and_delete(self._selected_snippets())
