"""DanmakuStudio — 弹幕压制引擎

将 XML/LRC 弹幕文件渲染并叠加到视频上，生成带弹幕的视频文件。
"""

from .config.models import (
    DEFAULT_CONFIG,
    AnimationParams,
    DanmakuConfig,
    EmojiStyle,
    EncodeMode,
    EncodeParams,
    LayoutRatio,
    LayoutStyle,
    StyleOverrides,
    SystemParams,
    TextStyle,
    apply_style_overrides,
)
from .core.burner import DanmakuBurner
from .input.parser import (
    parse_lrc,
    parse_subtitle,
    parse_subtitle_with_emoji,
    parse_xml,
)
from .render.renderer import DanmakuRenderer

__all__ = [
    "DEFAULT_CONFIG",
    "AnimationParams",
    "DanmakuBurner",
    "DanmakuConfig",
    "DanmakuRenderer",
    "EmojiStyle",
    "EncodeMode",
    "EncodeParams",
    "LayoutRatio",
    "LayoutStyle",
    "StyleOverrides",
    "SystemParams",
    "TextStyle",
    "apply_style_overrides",
    "parse_lrc",
    "parse_subtitle",
    "parse_subtitle_with_emoji",
    "parse_xml",
]
