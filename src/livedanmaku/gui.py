"""LiveDanmaku 图形界面兼容入口。"""

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
from .ui.main_window import LiveDanmakuWindow
from .ui.theme import apply_theme
from .ui.worker import ANSI_ESCAPE_RE, BatchWorker, QtLogStream

__all__ = [
    "BatchWorker",
    "LiveDanmakuWindow",
    "FileFolderPickerDialog",
    "QtLogStream",
    "get_app_dir",
    "main",
]


def main() -> None:
    from .logger_config import configure_logger
    from .utils.helpers import prepend_venv_scripts_to_path

    # pip 装进 venv 的 yt-dlp 需要让子进程按裸名找到（pythonw 启动时
    # venv Scripts 不在继承的 PATH 里）
    prepend_venv_scripts_to_path()
    # 尽早初始化日志（pythonw / 窗口模式下无控制台，落文件排障）
    configure_logger()
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication(sys.argv)
    apply_theme(app)
    window = LiveDanmakuWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
