"""DanmakuStudio 图形界面兼容入口。"""

from __future__ import annotations

import os
import sys

os.environ.setdefault(
    "QT_LOGGING_RULES",
    "qt.qpa.fonts=false;qt.text.font.db=false",
)

from PySide6.QtWidgets import QApplication

from .ui._shared import (
    dialog_start_dir_from_path as _dialog_start_dir_from_path,
    format_duration as _format_duration,
    get_app_dir,
    last_dir_path as _last_dir_path,
)
from .ui.file_picker import (
    ITEM_KIND_ROLE,
    ITEM_PATH_ROLE,
    FileFolderPickerDialog,
    video_name_filters as _video_name_filters,
)
from .ui.main_window import DanmakuStudioWindow
from .ui.theme import apply_theme
from .ui.worker import ANSI_ESCAPE_RE, BatchWorker, QtLogStream

__all__ = [
    "BatchWorker",
    "DanmakuStudioWindow",
    "FileFolderPickerDialog",
    "QtLogStream",
    "get_app_dir",
    "main",
]


def main() -> None:
    from .logger_config import configure_logger

    # 尽早初始化日志（pythonw / 窗口模式下无控制台，落文件排障）
    configure_logger()
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication(sys.argv)
    apply_theme(app)
    window = DanmakuStudioWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
