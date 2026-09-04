"""文件和文件夹混合选择对话框。"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QDir, QEvent, QModelIndex, QStandardPaths, Qt, QTimer
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFileSystemModel,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from ..utils.validation import SUPPORTED_VIDEO_EXTS
from ._shared import get_app_dir


def video_name_filters() -> list[str]:
    filters: list[str] = []
    for suffix in sorted(SUPPORTED_VIDEO_EXTS):
        filters.append(f"*{suffix}")
        filters.append(f"*{suffix.upper()}")
    return filters


ITEM_KIND_ROLE = int(Qt.ItemDataRole.UserRole) + 1
ITEM_PATH_ROLE = int(Qt.ItemDataRole.UserRole) + 2


class FileFolderPickerDialog(QDialog):
    """文件/文件夹混合多选对话框。"""

    def __init__(self, parent: QWidget | None = None, start_dir: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("选择视频或文件夹")
        self.resize(920, 620)
        self.setMinimumSize(720, 500)
        self._selected_paths: list[str] = []
        self._location_kind = "path"
        self._ignore_next_accept = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title = QLabel("选择视频或文件夹")
        title.setObjectName("pageTitle")
        hint = QLabel("支持多选视频；选择文件夹时会递归查找，并自动匹配同名 XML/LRC。")
        hint.setObjectName("dialogHint")
        layout.addWidget(title)
        layout.addWidget(hint)

        top_layout = QHBoxLayout()
        top_layout.setSpacing(8)
        self.btn_up = QPushButton("←  上一级")
        self.btn_current = QPushButton("＋  使用当前文件夹")
        self.path_label = QPushButton()
        self.path_label.setObjectName("pathButton")
        self.path_label.setCursor(Qt.CursorShape.PointingHandCursor)
        top_layout.addWidget(self.btn_up)
        top_layout.addWidget(self.btn_current)
        top_layout.addWidget(self.path_label, 1)
        layout.addLayout(top_layout)

        self.fs_model = QFileSystemModel(self)
        self.fs_model.setFilter(
            QDir.AllDirs | QDir.Files | QDir.NoDotAndDotDot | QDir.Drives
        )
        self.fs_model.setNameFilters(video_name_filters())
        self.fs_model.setNameFilterDisables(False)
        self.fs_model.setRootPath("")

        self.desktop_model = QStandardItemModel(self)
        self.desktop_model.setHorizontalHeaderLabels(["名称"])

        self.tree = QTreeView(self)
        self.tree.setObjectName("fileTree")
        self.tree.setModel(self.fs_model)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tree.setAlternatingRowColors(True)
        self.tree.setAnimated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, Qt.AscendingOrder)
        self.tree.setColumnWidth(0, 420)
        self.tree.viewport().installEventFilter(self)
        layout.addWidget(self.tree, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if ok_button is not None:
            ok_button.setText("确认选择")
            ok_button.setObjectName("primaryButton")
        if cancel_button is not None:
            cancel_button.setText("取消")
        for button in buttons.buttons():
            button.setAutoDefault(False)
            button.setDefault(False)
        layout.addWidget(buttons)

        self.btn_up.clicked.connect(self.go_up)
        self.btn_current.clicked.connect(self.select_current_folder)
        self.path_label.clicked.connect(self.choose_root_folder)
        self.tree.doubleClicked.connect(self.open_tree_item)
        buttons.accepted.connect(self.accept_selection)
        buttons.rejected.connect(self.reject)

        self._init_root(start_dir)

    def eventFilter(self, watched, event) -> bool:
        if (
            watched is self.tree.viewport()
            and event.type() == QEvent.Type.MouseButtonDblClick
        ):
            pos = (
                event.position().toPoint()
                if hasattr(event, "position")
                else event.pos()
            )
            index = self.tree.indexAt(pos)
            if index.isValid() and self.tree.model() is self.desktop_model:
                return self.open_desktop_item(index)

        return super().eventFilter(watched, event)

    def _init_root(self, start_dir: str | None) -> None:
        if start_dir and os.path.isdir(start_dir):
            start_path = os.path.abspath(start_dir)
        else:
            start_path = str(get_app_dir())

        self.set_root(start_path)
        start_index = self.fs_model.index(start_path)
        if start_index.isValid():
            self.tree.expand(start_index)
            self.tree.scrollTo(start_index)

    @staticmethod
    def desktop_directory() -> str:
        desktop = QStandardPaths.writableLocation(QStandardPaths.DesktopLocation)
        if desktop and os.path.isdir(desktop):
            return desktop

        fallback = Path.home() / "Desktop"
        if fallback.is_dir():
            return str(fallback)

        return str(Path.home())

    def suppress_next_accept(self) -> None:
        self._ignore_next_accept = True
        QTimer.singleShot(250, self._clear_ignore_next_accept)

    def set_computer_root(self) -> None:
        self._location_kind = "computer"
        self.tree.setModel(self.fs_model)
        self.tree.setSortingEnabled(True)
        self.tree.setRootIndex(QModelIndex())
        self.tree.sortByColumn(0, Qt.AscendingOrder)
        self.path_label.setText("此电脑")

    def _clear_ignore_next_accept(self) -> None:
        self._ignore_next_accept = False

    def set_desktop_root(self) -> None:
        desktop = self.desktop_directory()
        if not os.path.isdir(desktop):
            return

        self._location_kind = "desktop"
        self.tree.setSortingEnabled(False)
        self._rebuild_desktop_model()
        self.tree.setModel(self.desktop_model)
        self.tree.setRootIndex(QModelIndex())
        self.tree.setColumnWidth(0, 420)
        self.path_label.setText("桌面")

    def _desktop_item(self, name: str, kind: str, path: str = "") -> QStandardItem:
        item = QStandardItem(name)
        item.setEditable(False)
        item.setData(kind, ITEM_KIND_ROLE)
        item.setData(path, ITEM_PATH_ROLE)
        return item

    def _rebuild_desktop_model(self) -> None:
        self.desktop_model.clear()
        self.desktop_model.setHorizontalHeaderLabels(["名称"])
        self.desktop_model.appendRow(self._desktop_item("此电脑", "computer"))

        desktop = Path(self.desktop_directory())
        try:
            children = sorted(desktop.iterdir(), key=lambda path: path.name.lower())
        except OSError:
            return

        for child in children:
            if child.is_dir() or (
                child.is_file() and child.suffix.lower() in SUPPORTED_VIDEO_EXTS
            ):
                self.desktop_model.appendRow(
                    self._desktop_item(child.name, "path", str(child))
                )

    def set_root(self, path: str) -> None:
        if not path:
            self.set_computer_root()
            return

        desktop = os.path.normcase(os.path.abspath(self.desktop_directory()))
        normalized = os.path.normcase(os.path.abspath(path))
        if normalized == desktop:
            self.set_desktop_root()
            return

        index = self.fs_model.index(path)
        if index.isValid():
            self._location_kind = "path"
            self.tree.setModel(self.fs_model)
            self.tree.setSortingEnabled(True)
            self.tree.setRootIndex(index)
            self.tree.sortByColumn(0, Qt.AscendingOrder)
            display_path = self.fs_model.filePath(index) or "桌面"
            self.path_label.setText(QDir.toNativeSeparators(display_path))

    @staticmethod
    def parent_directory(path: str) -> str:
        normalized = os.path.abspath(path)
        drive, _ = os.path.splitdrive(normalized)
        stripped = normalized.rstrip("\\/")
        if drive and stripped.upper() == drive.upper():
            return ""

        parent = os.path.dirname(stripped)
        return parent if parent and parent != stripped else ""

    def current_directory(self) -> str:
        if self._location_kind == "computer":
            return ""
        if self._location_kind == "desktop":
            return self.desktop_directory()

        current = self.fs_model.filePath(self.tree.rootIndex())
        return current if current else ""

    def go_up(self) -> None:
        if self._location_kind == "desktop":
            return
        if self._location_kind == "computer":
            self.set_desktop_root()
            return

        current = self.current_directory()
        if not current:
            return

        parent = self.parent_directory(current)
        if parent:
            self.set_root(parent)
        else:
            self.set_computer_root()

    def choose_root_folder(self) -> None:
        start_dir = self.current_directory() or self.desktop_directory()
        folder = QFileDialog.getExistingDirectory(
            self,
            "选择文件夹",
            start_dir,
            QFileDialog.Option.ShowDirsOnly,
        )
        if folder:
            self.finish_selection([folder])

    def open_desktop_item(self, index: QModelIndex) -> bool:
        kind = index.data(ITEM_KIND_ROLE)
        if kind == "computer":
            self.suppress_next_accept()
            self.set_computer_root()
            return True

        path = index.data(ITEM_PATH_ROLE)
        if not path:
            return True

        candidate = Path(path)
        if candidate.is_dir():
            self.suppress_next_accept()
            self.set_root(path)
            return True

        if candidate.is_file() and candidate.suffix.lower() in SUPPORTED_VIDEO_EXTS:
            self.finish_selection([path])
            return True

        return True

    def open_tree_item(self, index: QModelIndex) -> None:
        if self.tree.model() is self.desktop_model:
            self.open_desktop_item(index)
            return
        else:
            path = self.fs_model.filePath(index)

        if not path:
            return

        candidate = Path(path)
        if candidate.is_dir():
            self.suppress_next_accept()
            self.set_root(path)
            return

        if candidate.is_file() and candidate.suffix.lower() in SUPPORTED_VIDEO_EXTS:
            self.finish_selection([path])

    def select_current_folder(self) -> None:
        current = self.current_directory()
        if current and os.path.isdir(current):
            self.finish_selection([current])
            return

        QMessageBox.warning(self, "提示", "当前位置不是可选择的文件夹。")

    def selected_paths(self) -> list[str]:
        return self._selected_paths

    def finish_selection(self, paths: list[str]) -> None:
        selected = sorted(
            {os.path.abspath(path) for path in paths if path and os.path.exists(path)}
        )
        if not selected:
            return

        self._ignore_next_accept = False
        self._selected_paths = selected
        super().accept()

    def accept(self) -> None:
        if self._ignore_next_accept:
            self._ignore_next_accept = False
            return

        if self._selected_paths:
            super().accept()

    def accept_selection(self) -> None:
        if self._ignore_next_accept:
            self._ignore_next_accept = False
            return

        indexes = self.tree.selectionModel().selectedRows(0)
        selected: list[str] = []

        for index in indexes:
            if self.tree.model() is self.desktop_model:
                kind = index.data(ITEM_KIND_ROLE)
                if kind == "computer":
                    if len(indexes) == 1:
                        self.suppress_next_accept()
                        self.set_computer_root()
                        return
                    continue
                path = index.data(ITEM_PATH_ROLE)
                if len(indexes) == 1 and path and Path(path).is_dir():
                    self.suppress_next_accept()
                    self.set_root(path)
                    return
            else:
                path = self.fs_model.filePath(index)

            if not path:
                continue
            candidate = Path(path)
            if candidate.is_dir() or (
                candidate.is_file() and candidate.suffix.lower() in SUPPORTED_VIDEO_EXTS
            ):
                selected.append(os.path.abspath(path))

        selected = sorted(set(selected))
        if not selected:
            self.select_current_folder()
            return

        self.finish_selection(selected)
