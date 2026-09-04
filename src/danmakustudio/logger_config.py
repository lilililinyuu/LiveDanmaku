"""日志配置模块

统一管理 loguru 日志的初始化配置，供 CLI 入口共用。
"""

import sys
from pathlib import Path

from loguru import logger

_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"

_STDERR_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level}</level> - "
    "<level>{message}</level>"
)

_FILE_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
    "{level} | "
    "{name}:{function}:{line} - {message}"
)


def configure_logger() -> None:
    """配置 loguru 日志。

    - stderr：INFO 级别，终端中彩色输出，GUI/重定向时输出纯文本
    - 文件：ffmpeg.log，DEBUG 级别，含源码位置，供故障排查
    """
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger.remove()

    isatty = getattr(sys.stderr, "isatty", None)
    colorize = bool(isatty and isatty())

    logger.add(
        sink=sys.stderr,
        format=_STDERR_FORMAT,
        level="INFO",
        colorize=colorize,
        enqueue=True,
    )

    logger.add(
        sink=str(_LOG_DIR / "ffmpeg.log"),
        format=_FILE_FORMAT,
        level="DEBUG",
        enqueue=True,
        rotation="10 MB",
        retention=7,
        encoding="utf-8",
    )
