"""通用工具函数"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

# Emoji 标记正则表达式
EMOJI_PATTERN = re.compile(r'\[([^\]]+)\]')

_PATH_PREPENDED_MARKER = "_LIVEDANMAKU_PATH_PREPENDED"


def prepend_venv_scripts_to_path() -> None:
    """把当前解释器环境的 Scripts 目录加到 PATH 最前（每进程一次）。

    pip 安装的 yt-dlp.exe 落在 .venv\\Scripts\\，而 GUI 通过
    pythonw（不激活 venv）启动时子进程继承的 PATH 不含该目录，
    按 `yt-dlp` 裸名调用会找不到。启动即前置该目录，则 setup.ps1
    装好的 yt-dlp 无需任何配置即可被下载/录制页使用。
    打包 EXE 模式下 sys.prefix 下没有 Scripts，自动跳过。
    """
    if os.environ.get(_PATH_PREPENDED_MARKER):
        return
    os.environ[_PATH_PREPENDED_MARKER] = "1"
    scripts = Path(sys.prefix) / "Scripts"
    exe = scripts / ("yt-dlp.exe" if os.name == "nt" else "yt-dlp")
    if exe.is_file():
        os.environ["PATH"] = str(scripts) + os.pathsep + os.environ.get("PATH", "")


def extract_emoji_names(text: str) -> list[str]:
    """提取文本中所有 Emoji 名称"""
    return [m.group(1) for m in EMOJI_PATTERN.finditer(text)]


def ensure_qt_app() -> QApplication:
    """确保 QApplication 已创建"""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    if isinstance(app, QCoreApplication) and not isinstance(app, QApplication):
        app = QApplication([])
    return app