"""图形界面入口。

DanmakuStudio 仅提供图形界面：压制、预览图、测试片段、yt-dlp 下载、
JSON 转 XML 与高潮点分析等全部功能都在窗口内操作。
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault(
    "QT_LOGGING_RULES",
    "qt.qpa.fonts=false;qt.text.font.db=false",
)

# GBK 等控制台无法编码弹幕文本中的 emoji 时会崩溃，降级为替换字符
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        try:
            _reconfigure(errors="replace")
        except ValueError:
            pass


def main() -> None:
    """启动图形界面。"""
    from .gui import main as gui_main

    gui_main()


if __name__ == "__main__":
    main()
