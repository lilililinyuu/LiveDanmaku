"""配置数据模型

所有配置相关的数据类定义。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum

# =============================================================================
# 编码模式
# =============================================================================

class EncodeMode(StrEnum):
    AUTO = "auto"
    GPU = "gpu"
    QSV = "qsv"
    CPU = "cpu"


# =============================================================================
# 校验辅助函数
# =============================================================================

def _assert_non_negative(obj: object, *names: str) -> None:
    for name in names:
        v = getattr(obj, name)
        if v < 0:
            raise ValueError(f"{name} 不能为负数，当前 {v}")


def _assert_positive(obj: object, *names: str) -> None:
    for name in names:
        v = getattr(obj, name)
        if v <= 0:
            raise ValueError(f"{name} 必须 > 0，当前 {v}")


# =============================================================================
# 子配置
# =============================================================================

@dataclass(frozen=True)
class TextStyle:
    """文本样式（普通用户或会员分组）"""
    color: str = "#FFFFFF"             # 字体颜色
    opacity: float = 1.0               # 文本不透明度（含描边）0~1
    outline_color: str = ""            # 描边颜色，空字符串表示禁用
    outline_width: float = 0.0         # 描边厚度（像素，可为小数），0表示禁用
    shadow_color: str = ""             # 阴影颜色，空字符串表示禁用
    shadow_opacity: float = 0.0        # 阴影不透明度 0~1

    def __post_init__(self):
        if not self.color:
            raise ValueError("color 不能为空字符串")
        for name in ("opacity", "shadow_opacity"):
            v = getattr(self, name)
            if not (0.0 <= v <= 1.0):
                raise ValueError(f"{name} 必须在 [0, 1] 范围内，当前 {v}")
        if self.outline_width < 0:
            raise ValueError(f"outline_width 不能为负数，当前 {self.outline_width}")


@dataclass(frozen=True)
class EmojiStyle:
    """表情样式（普通与会员共用）"""
    opacity: float = 1.0               # 表情不透明度 0~1
    size_ratio: float = 1.0            # 表情大小比例（相对行高），1.0=与行高一致
    spacing: int = 4                   # 表情间距（像素）

    def __post_init__(self):
        if not (0.0 <= self.opacity <= 1.0):
            raise ValueError(f"opacity 必须在 [0, 1] 范围内，当前 {self.opacity}")
        if not (0.0 < self.size_ratio <= 2.0):
            raise ValueError(f"size_ratio 必须在 (0, 2] 范围内，当前 {self.size_ratio}")
        if self.spacing < 0:
            raise ValueError(f"spacing 不能为负数，当前 {self.spacing}")


@dataclass(frozen=True)
class LayoutStyle:
    """布局样式配置

    内置默认值即项目当前推荐的样式（与仓库 danmakustudio.yaml 一致）。
    """
    danmaku_x: int = 20
    layer_width_extra: int = 100      # 已废弃：渲染层始终延伸至视频最右端，保留仅为兼容旧配置
    row_vertical_gap: int = 1         # 弹幕行之间的垂直间距
    multiline_row_gap: int = 1        # 多行文本行间距
    font_family: str = "Microsoft YaHei"  # 字体类型，空=仅使用内置回退字体族
    font_size: int = 25
    text: TextStyle = field(default_factory=lambda: TextStyle(
        opacity=0.7, outline_color="#246542", outline_width=0.7,
        shadow_color="#000000", shadow_opacity=0.7,
    ))          # 普通用户文本样式
    member_text: TextStyle = field(default_factory=lambda: TextStyle(
        color="#45dd7f", opacity=0.7, outline_color="#246542",
        outline_width=0.7, shadow_color="#000000", shadow_opacity=0.7,
    ))  # 会员文本样式
    emoji: EmojiStyle = field(default_factory=EmojiStyle)       # 表情样式

    def __post_init__(self):
        _assert_non_negative(self, "row_vertical_gap", "multiline_row_gap",
                             "danmaku_x", "layer_width_extra")
        _assert_positive(self, "font_size")


@dataclass(frozen=True)
class LayoutRatio:
    """布局比例配置"""
    max_text_rows: int = 4
    text_width_ratio: float = 0.8
    top_margin: int = 12                # 弹幕区顶部边距（像素）
    bottom_margin: int = 22             # 底部边距（像素）

    def __post_init__(self):
        _assert_non_negative(self, "max_text_rows", "top_margin", "bottom_margin")
        if not (0 < self.text_width_ratio <= 1.0):
            raise ValueError(f"text_width_ratio 必须在 (0, 1] 范围内，当前 {self.text_width_ratio}")


@dataclass(frozen=True)
class AnimationParams:
    """动画参数配置

    弹幕严格按自身时间点发射（无间隔/批次节流），
    仅受 max_text_rows 行数预算限制（单帧溢出时最多延迟一帧）。
    """
    first_danmaku_at: float | None = None  # 第一条弹幕对齐到该视频秒数；None=保持原时间轴

    def __post_init__(self):
        if self.first_danmaku_at is not None and self.first_danmaku_at < 0:
            raise ValueError(f"first_danmaku_at 不能为负数，当前 {self.first_danmaku_at}")


@dataclass(frozen=True)
class EncodeParams:
    """编码参数配置"""
    gpu_preset: str = "p5"
    gpu_cq: int = 15
    qsv_preset: str = "slow"
    qsv_quality: int = 18
    cpu_preset: str = "medium"
    cpu_crf: int = 18
    cpu_min_reserve_threads: int = 2
    extra_args: list[str] = field(default_factory=list)  # 追加到 ffmpeg 命令末尾的自定义参数

    def __post_init__(self):
        for name in ("gpu_cq", "qsv_quality", "cpu_crf"):
            v = getattr(self, name)
            if not (0 <= v <= 51):
                raise ValueError(f"{name} 必须在 [0, 51] 范围内，当前 {v}")
        _assert_positive(self, "cpu_min_reserve_threads")
        for name in ("gpu_preset", "qsv_preset", "cpu_preset"):
            if not getattr(self, name):
                raise ValueError(f"{name} 不能为空字符串")


@dataclass(frozen=True)
class SystemParams:
    """系统参数配置"""
    pipe_buffer_size: int = 10_000_000
    pipe_queue_size: int = 16
    ffmpeg_timeout: int = 10
    stderr_thread_timeout: int = 5
    video_alignment: int = 2
    max_queue_frames: int = 64

    def __post_init__(self):
        _assert_positive(self, "pipe_buffer_size", "pipe_queue_size",
                         "ffmpeg_timeout", "stderr_thread_timeout",
                         "max_queue_frames")
        if self.video_alignment <= 0 or (self.video_alignment & (self.video_alignment - 1)) != 0:
            raise ValueError(
                f"video_alignment 必须是 2 的幂，当前 {self.video_alignment}"
            )


# =============================================================================
# 聚合配置
# =============================================================================

@dataclass(frozen=True)
class DanmakuConfig:
    """弹幕压制完整配置"""
    style: LayoutStyle = field(default_factory=LayoutStyle)
    ratio: LayoutRatio = field(default_factory=LayoutRatio)
    animation: AnimationParams = field(default_factory=AnimationParams)
    encode: EncodeParams = field(default_factory=EncodeParams)
    system: SystemParams = field(default_factory=SystemParams)


# =============================================================================
# 样式覆盖
# =============================================================================

@dataclass
class StyleOverrides:
    """GUI 样式面板提供的覆盖项，None 表示不覆盖。"""
    font_family: str | None = None
    font_size: int | None = None
    text: TextStyle | None = None
    member_text: TextStyle | None = None
    emoji: EmojiStyle | None = None


def apply_style_overrides(config: DanmakuConfig, overrides: StyleOverrides) -> DanmakuConfig:
    """把样式覆盖项合并进配置，返回新配置（原配置不变）。"""
    style = config.style
    changes: dict[str, object] = {}
    for name in ("font_family", "font_size", "text", "member_text", "emoji"):
        value = getattr(overrides, name)
        if value is not None:
            changes[name] = value
    if not changes:
        return config
    return replace(config, style=replace(style, **changes))


# =============================================================================
# 默认值
# =============================================================================

DEFAULT_CONFIG = DanmakuConfig()