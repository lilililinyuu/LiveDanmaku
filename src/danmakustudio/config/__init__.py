"""配置模块

统一导出配置相关类和函数。
"""

from .loader import load_config
from .models import (
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

__all__ = [
    "DEFAULT_CONFIG",
    "AnimationParams",
    "DanmakuConfig",
    "EmojiStyle",
    "EncodeMode",
    "EncodeParams",
    "LayoutRatio",
    "LayoutStyle",
    "StyleOverrides",
    "SystemParams",
    "TextStyle",
    "apply_style_overrides",
    "load_config",
]