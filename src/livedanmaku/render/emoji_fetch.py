"""YouTube 表情图片的运行时下载与磁盘缓存。

表情名 -> 图片 URL 由弹幕 XML 中的 <e name url/> 标签提供
（由 json2xml.py 从 YouTube 直播回放 JSON 提取）。
首次下载后缓存到用户目录，后续压制离线可用。
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

from loguru import logger

_DEFAULT_TIMEOUT = 15.0


def emoji_cache_dir() -> Path:
    """表情图片的磁盘缓存目录（用户目录）。"""
    return Path.home() / ".livedanmaku_emoji_cache"


def download_emoji(name: str, url: str, cache_dir: Path | None = None) -> Path | None:
    """下载表情图片到磁盘缓存，返回文件路径；失败返回 None。

    已缓存时直接返回缓存路径，不再发起网络请求。
    """
    directory = cache_dir or emoji_cache_dir()
    target = directory / f"{name}.png"
    if target.exists():
        return target
    if not url.lower().startswith(("http://", "https://")):
        return None
    try:
        with urllib.request.urlopen(url, timeout=_DEFAULT_TIMEOUT) as resp:
            data = resp.read()
        directory.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    except (OSError, ValueError) as e:
        logger.warning(f"表情下载失败 [{name}]: {e}")
        return None
    return target
