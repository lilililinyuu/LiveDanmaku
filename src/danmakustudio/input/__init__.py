"""输入处理模块

负责 XML/LRC 解析和弹幕事件数据模型。
"""

from .event import DanmakuEvent
from .parser import parse_lrc, parse_subtitle, parse_subtitle_with_emoji, parse_xml

__all__ = [
    "DanmakuEvent",
    "parse_lrc",
    "parse_subtitle",
    "parse_subtitle_with_emoji",
    "parse_xml",
]
