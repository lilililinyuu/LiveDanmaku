"""GUI 组件共享的轻量辅助函数。"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def get_app_dir() -> Path:
    """返回程序目录，兼容源码运行和 PyInstaller。"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def last_dir_path() -> Path:
    return Path.home() / ".livedanmaku_last_dir.txt"


def last_config_dir_path() -> Path:
    return Path.home() / ".livedanmaku_last_config_dir.txt"


def gui_style_path() -> Path:
    return Path.home() / ".livedanmaku_gui_style.json"


def source_project_dir() -> Path:
    """返回源码项目目录；打包后不可用时回退到当前目录。"""
    candidate = Path(__file__).resolve().parents[3]
    if (candidate / "pyproject.toml").is_file():
        return candidate
    return Path.cwd()


def dialog_start_dir_from_path(path_text: str, fallback: str) -> str:
    """根据输入框路径推导文件对话框的起始目录。"""
    raw_path = os.path.expandvars(os.path.expanduser(path_text.strip()))
    if raw_path:
        path = Path(raw_path)
        if path.is_file():
            return str(path.parent)
        if path.is_dir():
            return str(path)
        if path.parent.is_dir():
            return str(path.parent)

    if fallback and os.path.isdir(fallback):
        return fallback

    return str(get_app_dir())


def config_dialog_start_location(path_text: str, last_config_dir: str = "") -> str:
    """按配置文件场景的优先级返回打开对话框的初始位置。"""
    raw_path = os.path.expandvars(os.path.expanduser(path_text.strip()))
    if raw_path:
        selected_path = Path(raw_path)
        if selected_path.is_file():
            # 传入完整文件名，让 QFileDialog 打开后直接高亮当前选择。
            return str(selected_path)
        if selected_path.is_dir():
            return str(selected_path)
        if selected_path.parent.is_dir():
            return str(selected_path)

    default_dirs = (
        Path(last_config_dir) if last_config_dir else None,
        get_app_dir(),
        source_project_dir(),
        Path(sys.executable).resolve().parent,
        Path.home(),
    )
    for candidate in default_dirs:
        if candidate is not None and candidate.is_dir():
            return str(candidate)

    return str(Path.cwd())


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"
