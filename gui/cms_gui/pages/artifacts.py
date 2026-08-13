"""Artifacts page: the run's report tree, with a preview.

Fed by two events - ``run.dir`` names the run directory, ``artifacts.written``
announces each scenario's files as they land - so the tree fills in during the
run rather than after it. Selecting a file previews it (text inline, images
scaled); "Open in OS" hands it to the desktop's own handler.
"""

import json
import os

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (QLabel, QPlainTextEdit, QPushButton, QScrollArea,
                               QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from .. import theme, widgets

TEXT_SUFFIXES = (".json", ".log", ".txt", ".html", ".yaml", ".yml", ".md", ".csv")
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp")
PREVIEW_LIMIT = 400_000     # bytes of a text file worth putting on screen


class ArtifactsPage(QWidget):
    """A tree of the report directory plus a preview of the selected file."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._run_dir = ""

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        head = QWidget()
        head.setStyleSheet("border-bottom: 1px solid %s;" % theme.DIVIDER)
        head_layout = QVBoxLayout(head)
        head_layout.setContentsMargins(24, 14, 24, 12)
        self.run_label = widgets.mono("no run directory yet")
        self.open_dir = QPushButton("Open folder")
        self.open_dir.clicked.connect(self._open_run_dir)
        self.open_dir.setEnabled(False)
        refresh = QPushButton("Rescan")
        refresh.clicked.connect(self.rescan)
        head_layout.addWidget(widgets.row(widgets.heading("Artifacts"),
                                          self.run_label, None, refresh,
                                          self.open_dir))
        column.addWidget(head)

        splitter = QSplitter(Qt.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["File", "Size"])
        self.tree.setColumnWidth(0, 300)
        self.tree.itemSelectionChanged.connect(self._preview_selected)
        splitter.addWidget(self.tree)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        path_bar = QWidget()
        path_bar.setStyleSheet("border-bottom: 1px solid %s;" % theme.DIVIDER)
        path_layout = QVBoxLayout(path_bar)
        path_layout.setContentsMargins(16, 10, 16, 10)
        self.path_label = widgets.mono("")
        self.open_file = QPushButton("Open in OS")
        self.open_file.clicked.connect(self._open_selected)
        self.open_file.setEnabled(False)
        path_layout.addWidget(widgets.row(self.path_label, None, self.open_file))
        right_layout.addWidget(path_bar)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setStyleSheet(
            "background: %s; border: none; font-family: %s; font-size: 12px;"
            % (theme.NEUTRAL[100], theme.MONO_CSS))
        right_layout.addWidget(self.text, 1)

        self.image_area = QScrollArea()
        self.image_area.setWidgetResizable(True)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignCenter)
        self.image_area.setWidget(self.image)
        self.image_area.setVisible(False)
        right_layout.addWidget(self.image_area, 1)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([340, 700])
        column.addWidget(splitter, 1)

    # -- events ---------------------------------------------------------------
    def set_run_dir(self, path):
        self._run_dir = path or ""
        self.run_label.setText(self._run_dir or "no run directory yet")
        self.open_dir.setEnabled(bool(self._run_dir and os.path.isdir(self._run_dir)))
        self.rescan()

    def note_artifacts(self, _event):
        """A scenario finished writing; the cheapest correct answer is a rescan."""
        self.rescan()

    def rescan(self):
        selected = self._selected_path()
        self.tree.clear()
        if not self._run_dir or not os.path.isdir(self._run_dir):
            return
        root = QTreeWidgetItem([os.path.basename(self._run_dir.rstrip(os.sep)) or
                                self._run_dir, ""])
        root.setData(0, Qt.UserRole, self._run_dir)
        root.setFont(0, theme.mono_font(9))
        self.tree.addTopLevelItem(root)
        self._fill(root, self._run_dir)
        root.setExpanded(True)
        if selected:
            self._select_path(selected)

    def _fill(self, parent_item, directory):
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            return
        for name in names:
            path = os.path.join(directory, name)
            if os.path.isdir(path):
                item = QTreeWidgetItem([name + "/", ""])
                item.setData(0, Qt.UserRole, path)
                item.setFont(0, theme.mono_font(9))
                parent_item.addChild(item)
                self._fill(item, path)
                item.setExpanded(True)
            else:
                item = QTreeWidgetItem([name, _human_size(path)])
                item.setData(0, Qt.UserRole, path)
                item.setFont(0, theme.mono_font(9))
                item.setFont(1, theme.mono_font(8))
                parent_item.addChild(item)

    # -- preview --------------------------------------------------------------
    def _selected_path(self):
        items = self.tree.selectedItems()
        return items[0].data(0, Qt.UserRole) if items else ""

    def _select_path(self, path):
        iterator = self.tree.findItems("", Qt.MatchContains | Qt.MatchRecursive, 0)
        for item in iterator:
            if item.data(0, Qt.UserRole) == path:
                self.tree.setCurrentItem(item)
                return

    def _preview_selected(self):
        path = self._selected_path()
        self.path_label.setText(path)
        self.open_file.setEnabled(bool(path))
        if not path or os.path.isdir(path):
            self._show_text("" if not path else "Directory")
            return
        suffix = os.path.splitext(path)[1].lower()
        if suffix in IMAGE_SUFFIXES:
            pixmap = QPixmap(path)
            if not pixmap.isNull():
                self.text.setVisible(False)
                self.image_area.setVisible(True)
                self.image.setPixmap(pixmap.scaled(
                    1200, 900, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                return
        if suffix in TEXT_SUFFIXES:
            try:
                size = os.path.getsize(path)
                with open(path, encoding="utf-8", errors="replace") as fh:
                    body = fh.read(PREVIEW_LIMIT)
                if size > PREVIEW_LIMIT:
                    body += "\n\n… truncated (%s total)" % _human_size(path)
                if suffix == ".json":
                    try:
                        body = json.dumps(json.loads(body), indent=2, ensure_ascii=False)
                    except ValueError:
                        pass
                self._show_text(body)
                return
            except OSError as exc:
                self._show_text("Cannot read: %s" % exc)
                return
        self._show_text("(no preview for %s — use Open in OS)" % (suffix or "this file"))

    def _show_text(self, body):
        self.image_area.setVisible(False)
        self.text.setVisible(True)
        self.text.setPlainText(body)

    # -- opening --------------------------------------------------------------
    def _open_selected(self):
        path = self._selected_path()
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _open_run_dir(self):
        if self._run_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._run_dir))


def _human_size(path):
    try:
        size = os.path.getsize(path)
    except OSError:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return "%.0f %s" % (size, unit) if unit == "B" else "%.1f %s" % (size, unit)
        size /= 1024.0
    return ""
