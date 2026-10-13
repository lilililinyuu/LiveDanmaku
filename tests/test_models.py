"""models.py 单元测试

测试 DanmakuEvent、ActiveDanmaku 的构建、分段、折行和尺寸计算。
"""

import pytest
from PySide6.QtGui import QColor

from livedanmaku.config import DEFAULT_CONFIG
from livedanmaku.config.models import (
    AnimationParams,
    EmojiStyle,
    EncodeParams,
    LayoutStyle,
    StyleOverrides,
    TextStyle,
    apply_style_overrides,
)
from livedanmaku.input.event import DanmakuEvent
from livedanmaku.layout.active import ActiveDanmaku
from livedanmaku.render.layout_builder import DanmakuLayoutBuilder
from livedanmaku.render.segments import RenderSegment, TextRow

style = DEFAULT_CONFIG.style

MAX_CONTENT_WIDTH = 800


def _make_danmaku(
    text: str,
    font_metrics,
    emoji_cache: dict,
    max_content_width: int = MAX_CONTENT_WIDTH,
    line_height: int = 36,
) -> ActiveDanmaku:
    """创建 ActiveDanmaku 测试实例。"""
    event = DanmakuEvent(time=1.0, user="测试用户", text=text)
    builder = DanmakuLayoutBuilder(
        fm=font_metrics,
        emoji_cache=emoji_cache,
        max_content_width=max_content_width,
        line_height=line_height,
    )
    layout = builder.build(event)
    return ActiveDanmaku(event=event, layout=layout, x=style.danmaku_x)


# =============================================================================
# DanmakuEvent 数据类
# =============================================================================


class TestDanmakuEvent:
    """测试弹幕事件数据类的默认值和构造。"""

    def test_default_values(self):
        """会员标记默认为 False。"""
        e = DanmakuEvent(time=1.0, user="u", text="t")
        assert not e.is_membership

    def test_membership_event(self):
        """会员弹幕应正确保存会员标记。"""
        e = DanmakuEvent(time=2.0, user="u", text="t", is_membership=True)
        assert e.is_membership


# =============================================================================
# ActiveDanmaku - 普通弹幕
# =============================================================================


class TestActiveDanmakuNormal:
    """测试普通文本弹幕的构建、尺寸和行属性。"""

    def test_basic_construction(self, font_metrics, emoji_cache):
        """构造后应具有正尺寸、正确的 X 偏移和空缓存。"""
        dm = _make_danmaku("你好世界", font_metrics, emoji_cache)
        assert dm.event.text == "你好世界"
        assert dm.total_width > 0
        assert dm.height > 0
        assert dm.x == style.danmaku_x
        assert dm.cached_image is None

    def test_has_rows(self, font_metrics, emoji_cache):
        """任何弹幕应至少有一行渲染行。"""
        dm = _make_danmaku("测试", font_metrics, emoji_cache)
        assert len(dm.rows) >= 1

    def test_dimensions_without_padding(self, font_metrics, emoji_cache):
        """高度不小于行高，宽度不超过文本宽度。"""
        dm = _make_danmaku("测试", font_metrics, emoji_cache)
        assert dm.height >= 36
        assert dm.total_width <= MAX_CONTENT_WIDTH


# =============================================================================
# ActiveDanmaku - Emoji
# =============================================================================


class TestActiveDanmakuEmoji:
    """测试 Emoji 在弹幕文本中的解析和段落生成。"""

    def test_emoji_in_text(self, font_metrics, emoji_cache):
        """已缓存的 Emoji 应生成 emoji 段落。"""
        emoji_name = next(iter(emoji_cache), None)
        if emoji_name is None:
            pytest.skip("无 Emoji 图片缓存")

        dm = _make_danmaku(f"你好[{emoji_name}]世界", font_metrics, emoji_cache)
        types = [seg.type for row in dm.rows for seg in row.segments]
        assert 'emoji' in types

    def test_unknown_emoji_as_text(self, font_metrics, emoji_cache):
        """未缓存的 Emoji 应作为纯文本段落处理。"""
        dm = _make_danmaku("你好[不存在的emoji]世界", font_metrics, emoji_cache)
        types = [seg.type for row in dm.rows for seg in row.segments]
        assert 'emoji' not in types
        text_contents = [seg.content for row in dm.rows for seg in row.segments
                         if seg.type == 'text']
        assert any("不存在的emoji" in t for t in text_contents)

    def test_text_between_emojis(self, font_metrics, emoji_cache):
        """两个 Emoji 之间的文本应保留为 text 段落。"""
        emoji_name = next(iter(emoji_cache), None)
        if emoji_name is None:
            pytest.skip("无 Emoji 图片缓存")

        dm = _make_danmaku(
            f"[{emoji_name}]中间文本[{emoji_name}]",
            font_metrics, emoji_cache,
        )
        types = [seg.type for row in dm.rows for seg in row.segments]
        emoji_count = types.count('emoji')
        assert emoji_count == 2
        text_contents = [seg.content for row in dm.rows for seg in row.segments
                         if seg.type == 'text']
        assert any("中间文本" in t for t in text_contents)


# =============================================================================
# ActiveDanmaku - 折行
# =============================================================================


class TestActiveDanmakuWrapping:
    """测试单行渲染：任意长度文本都保持一行，随滚动完整展示。"""

    def test_short_text_single_line(self, font_metrics, emoji_cache):
        dm = _make_danmaku("短", font_metrics, emoji_cache)
        assert len(dm.rows) == 1

    def test_long_text_single_line(self, font_metrics, emoji_cache):
        dm = _make_danmaku("这是一段非常长的文本" * 10,
                           font_metrics, emoji_cache,
                           max_content_width=200)
        assert len(dm.rows) == 1

    def test_narrow_width_still_single_line(self, font_metrics, emoji_cache):
        dm = _make_danmaku("你好世界", font_metrics, emoji_cache,
                           max_content_width=50)
        assert len(dm.rows) == 1


# =============================================================================
# ActiveDanmaku - 预渲染
# =============================================================================


class TestActiveDanmakuPreRender:
    """测试弹幕预渲染到缓存图片。"""

    def test_pre_render_creates_image(self, font_metrics, emoji_cache, font):
        """预渲染应创建有效的缓存图片。"""
        dm = _make_danmaku("测试预渲染", font_metrics, emoji_cache)
        assert dm.cached_image is None
        dm.pre_render(font, emoji_cache)
        assert dm.cached_image is not None
        assert not dm.cached_image.isNull()

    def test_pre_render_image_size(self, font_metrics, emoji_cache, font):
        """缓存图片尺寸应与弹幕尺寸一致。"""
        dm = _make_danmaku("测试", font_metrics, emoji_cache)
        dm.pre_render(font, emoji_cache)
        assert dm.cached_image is not None
        assert dm.cached_image.width() == dm.total_width
        assert dm.cached_image.height() == dm.height


# =============================================================================
# TextStyle
# =============================================================================


class TestTextStyle:
    """测试 TextStyle 的默认值与校验。"""

    def test_defaults(self):
        """内置默认样式与仓库 livedanmaku.yaml 保持一致。"""
        ts = DEFAULT_CONFIG.style.text
        assert ts.color == "#FFFFFF"
        assert ts.opacity == 0.7
        assert ts.outline_color == "#246542"
        assert ts.outline_width == 0.7
        assert ts.shadow_color == "#000000"
        assert ts.shadow_opacity == 0.7
        assert DEFAULT_CONFIG.style.member_text.color == "#45dd7f"

    @pytest.mark.parametrize(
        "field,value",
        [("opacity", 1.5), ("opacity", -0.1), ("shadow_opacity", 1.5)],
    )
    def test_opacity_out_of_range(self, field, value):
        with pytest.raises(ValueError):
            TextStyle(**{field: value})

    def test_outline_width_negative(self):
        with pytest.raises(ValueError):
            TextStyle(outline_width=-1)

    def test_empty_color_rejected(self):
        with pytest.raises(ValueError):
            TextStyle(color="")


# =============================================================================
# 会员/普通用户分组颜色
# =============================================================================


def _text_segment_colors(dm):
    return [
        seg.color
        for row in dm.rows
        for seg in row.segments
        if seg.type == 'text' and seg.color is not None
    ]


class TestMemberColor:
    """测试会员与普通用户使用各自的文本样式颜色。"""

    def _build(self, font_metrics, emoji_cache, style):
        builder = DanmakuLayoutBuilder(
            fm=font_metrics,
            emoji_cache=emoji_cache,
            max_content_width=MAX_CONTENT_WIDTH,
            line_height=36,
            style=style,
        )
        normal_ev = DanmakuEvent(time=1.0, user="u", text="测试")
        member_ev = DanmakuEvent(time=1.0, user="u", text="测试", is_membership=True)
        normal = ActiveDanmaku(event=normal_ev, layout=builder.build(normal_ev), x=0)
        member = ActiveDanmaku(event=member_ev, layout=builder.build(member_ev), x=0)
        return normal, member

    def test_default_group_colors(self, font_metrics, emoji_cache):
        normal, member = self._build(font_metrics, emoji_cache, DEFAULT_CONFIG.style)
        assert _text_segment_colors(normal) == [QColor("#FFFFFF")]
        assert _text_segment_colors(member) == [QColor("#45dd7f")]

    def test_custom_group_colors(self, font_metrics, emoji_cache):
        style = LayoutStyle(
            text=TextStyle(color="#FF0000"),
            member_text=TextStyle(color="#45dd7f"),
        )
        normal, member = self._build(font_metrics, emoji_cache, style)
        assert _text_segment_colors(normal) == [QColor("#FF0000")]
        assert _text_segment_colors(member) == [QColor("#45dd7f")]


# =============================================================================
# 文本阴影渲染
# =============================================================================


def _dark_pixels(img):
    """返回 (暗像素数, 暗像素坐标列表)。暗像素 = 近黑色且不透明。"""
    pixels = []
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.alpha() > 0 and c.red() < 64 and c.green() < 64 and c.blue() < 64:
                pixels.append((x, y))
    return len(pixels), pixels


def _is_bright(img, x, y):
    c = img.pixelColor(x, y)
    return c.alpha() > 0 and c.red() > 191 and c.green() > 191 and c.blue() > 191


class TestShadowRender:
    """测试文本阴影的预渲染像素效果。"""

    def _build_shadow_danmaku(self, font_metrics, emoji_cache, shadow_style):
        builder = DanmakuLayoutBuilder(
            fm=font_metrics,
            emoji_cache=emoji_cache,
            max_content_width=MAX_CONTENT_WIDTH,
            line_height=36,
            style=shadow_style,
        )
        event = DanmakuEvent(time=1.0, user="u", text="阴影测试文本")
        return ActiveDanmaku(event=event, layout=builder.build(event), x=0)

    def test_shadow_disabled_with_plain_style(self, font_metrics, emoji_cache, font):
        """未启用阴影的样式不应产生暗像素。"""
        style = LayoutStyle(text=TextStyle())
        dm = self._build_shadow_danmaku(font_metrics, emoji_cache, style)
        dm.pre_render(font, emoji_cache)
        count, _ = _dark_pixels(dm.cached_image)
        assert count == 0

    def test_shadow_draws_dark_offset(self, font_metrics, emoji_cache, font):
        """开启阴影后应出现暗像素，且与亮像素呈 (+2,+2) 偏移关系。"""
        style = LayoutStyle(text=TextStyle(shadow_color="#000000", shadow_opacity=1.0))
        dm = self._build_shadow_danmaku(font_metrics, emoji_cache, style)
        dm.pre_render(font, emoji_cache)
        assert dm.cached_image is not None
        count, pixels = _dark_pixels(dm.cached_image)
        assert count > 0
        assert any(
            _is_bright(dm.cached_image, x - 2, y - 2)
            for x, y in pixels
            if x >= 2 and y >= 2
        )

    def test_shadow_opacity_affects_alpha(self, font_metrics, emoji_cache, font):
        """shadow_opacity=0.5 时暗像素 alpha 应约为 127。"""
        style = LayoutStyle(text=TextStyle(shadow_color="#000000", shadow_opacity=0.5))
        dm = self._build_shadow_danmaku(font_metrics, emoji_cache, style)
        dm.pre_render(font, emoji_cache)
        assert dm.cached_image is not None
        _, pixels = _dark_pixels(dm.cached_image)
        assert pixels, "未检测到阴影像素"
        alphas = [dm.cached_image.pixelColor(x, y).alpha() for x, y in pixels]
        assert any(110 <= a <= 145 for a in alphas)

    def test_shadow_descender_not_clipped(self, font_metrics, emoji_cache, font):
        """小写字母 y 的下沉阴影不应被缓存图下边缘截断。"""
        style = LayoutStyle(text=TextStyle(shadow_color="#000000", shadow_opacity=1.0))
        builder = DanmakuLayoutBuilder(
            fm=font_metrics,
            emoji_cache=emoji_cache,
            max_content_width=MAX_CONTENT_WIDTH,
            line_height=36,
            style=style,
        )
        event = DanmakuEvent(time=1.0, user="u", text="y")
        dm = ActiveDanmaku(event=event, layout=builder.build(event), x=0)
        dm.pre_render(font, emoji_cache)
        assert dm.cached_image is not None
        # 阴影开启时高度应含 +2 垫底
        assert dm.cached_image.height() == 36 + 2
        # 垫底区域（y >= 36）应存在阴影像素（修复前该区域被截断）
        assert any(
            dm.cached_image.pixelColor(x, y).alpha() > 0
            and dm.cached_image.pixelColor(x, y).red() < 64
            for y in range(36, dm.cached_image.height())
            for x in range(dm.cached_image.width())
        ), "下沉阴影仍被截断"


# =============================================================================
# EmojiStyle
# =============================================================================


class TestEmojiStyle:
    """测试 EmojiStyle 的默认值与校验。"""

    def test_defaults(self):
        es = DEFAULT_CONFIG.style.emoji
        assert es.opacity == 1.0
        assert es.size_ratio == 1.0
        assert es.spacing == 4

    @pytest.mark.parametrize("field,value", [("opacity", 1.5), ("opacity", -0.1)])
    def test_opacity_out_of_range(self, field, value):
        with pytest.raises(ValueError):
            EmojiStyle(**{field: value})

    @pytest.mark.parametrize("value", [0.0, -0.5, 2.5])
    def test_size_ratio_out_of_range(self, value):
        with pytest.raises(ValueError):
            EmojiStyle(size_ratio=value)

    def test_spacing_negative(self):
        with pytest.raises(ValueError):
            EmojiStyle(spacing=-1)


# =============================================================================
# 表情间距与不透明度
# =============================================================================


class TestEmojiSpacing:
    """测试表情间距配置生效。"""

    def test_spacing_segment_width(self, font_metrics, emoji_cache):
        emoji_name = next(iter(emoji_cache), None)
        if emoji_name is None:
            pytest.skip("无 Emoji 图片缓存")
        style = LayoutStyle(emoji=EmojiStyle(spacing=10))
        builder = DanmakuLayoutBuilder(
            fm=font_metrics,
            emoji_cache=emoji_cache,
            max_content_width=MAX_CONTENT_WIDTH,
            line_height=36,
            style=style,
        )
        event = DanmakuEvent(time=1.0, user="u", text=f"文字[{emoji_name}]")
        layout = builder.build(event)
        spacings = [
            seg.width
            for row in layout.rows
            for seg in row.segments
            if seg.type == 'spacing'
        ]
        assert spacings and all(w == 10 for w in spacings)


class TestEmojiOpacity:
    """测试表情不透明度独立于文本不透明度。"""

    def _build_emoji_only(self, font_metrics, emoji_cache, emoji_name, opacity):
        style = LayoutStyle(
            text=TextStyle(opacity=1.0),
            emoji=EmojiStyle(opacity=opacity),
        )
        builder = DanmakuLayoutBuilder(
            fm=font_metrics,
            emoji_cache=emoji_cache,
            max_content_width=MAX_CONTENT_WIDTH,
            line_height=36,
            style=style,
        )
        event = DanmakuEvent(time=1.0, user="u", text=f"[{emoji_name}]")
        return ActiveDanmaku(event=event, layout=builder.build(event), x=0)

    @staticmethod
    def _max_alpha(img):
        return max(
            img.pixelColor(x, y).alpha()
            for y in range(img.height())
            for x in range(img.width())
        )

    def test_half_opacity(self, font_metrics, emoji_cache, font):
        emoji_name = next(iter(emoji_cache), None)
        if emoji_name is None:
            pytest.skip("无 Emoji 图片缓存")
        dm = self._build_emoji_only(font_metrics, emoji_cache, emoji_name, 0.5)
        dm.pre_render(font, emoji_cache)
        assert dm.cached_image is not None
        assert 110 <= self._max_alpha(dm.cached_image) <= 145

    def test_full_opacity(self, font_metrics, emoji_cache, font):
        emoji_name = next(iter(emoji_cache), None)
        if emoji_name is None:
            pytest.skip("无 Emoji 图片缓存")
        dm = self._build_emoji_only(font_metrics, emoji_cache, emoji_name, 1.0)
        dm.pre_render(font, emoji_cache)
        assert dm.cached_image is not None
        assert self._max_alpha(dm.cached_image) > 200


# =============================================================================
# 样式覆盖
# =============================================================================


class TestStyleOverrides:
    """测试 apply_style_overrides 的合并语义。"""

    def test_empty_overrides_returns_same_config(self):
        result = apply_style_overrides(DEFAULT_CONFIG, StyleOverrides())
        assert result is DEFAULT_CONFIG

    def test_partial_override(self):
        result = apply_style_overrides(DEFAULT_CONFIG, StyleOverrides(font_size=18))
        assert result.style.font_size == 18
        assert result.style.text == DEFAULT_CONFIG.style.text
        assert result.ratio == DEFAULT_CONFIG.ratio

    def test_full_override(self):
        new_text = TextStyle(color="#FF0000", opacity=0.5)
        new_member = TextStyle(color="#45dd7f")
        new_emoji = EmojiStyle(opacity=0.5, size_ratio=0.8, spacing=10)
        result = apply_style_overrides(DEFAULT_CONFIG, StyleOverrides(
            font_family="SimSun",
            font_size=30,
            text=new_text,
            member_text=new_member,
            emoji=new_emoji,
        ))
        assert result.style.font_family == "SimSun"
        assert result.style.font_size == 30
        assert result.style.text is new_text
        assert result.style.member_text is new_member
        assert result.style.emoji is new_emoji

    def test_original_config_not_mutated(self):
        apply_style_overrides(DEFAULT_CONFIG, StyleOverrides(font_size=18))
        assert DEFAULT_CONFIG.style.font_size == 25
        assert DEFAULT_CONFIG.style.text.color == "#FFFFFF"


def test_animation_first_danmaku_at_validation():
    """first_danmaku_at 默认 None，负数应报错。"""
    assert DEFAULT_CONFIG.animation.first_danmaku_at is None
    assert AnimationParams(first_danmaku_at=0.0).first_danmaku_at == 0.0
    with pytest.raises(ValueError):
        AnimationParams(first_danmaku_at=-1.0)


def test_encode_qsv_vendor_id_default_and_validation():
    """qsv_vendor_id 默认钉定 Intel (0x8086)，负数应报错。"""
    assert EncodeParams().qsv_vendor_id == 0x8086
    assert EncodeParams(qsv_vendor_id=0).qsv_vendor_id == 0
    with pytest.raises(ValueError):
        EncodeParams(qsv_vendor_id=-1)


# =============================================================================
# 描边不透明度跟随文本不透明度
# =============================================================================


class TestOutlineOpacity:
    """描边没有独立不透明度，应随文本不透明度一起缩放。"""

    def _build_outline_danmaku(self, font_metrics, emoji_cache, opacity):
        style = LayoutStyle(text=TextStyle(
            opacity=opacity,
            outline_color="#000000",
            outline_width=2.0,
        ))
        builder = DanmakuLayoutBuilder(
            fm=font_metrics,
            emoji_cache=emoji_cache,
            max_content_width=MAX_CONTENT_WIDTH,
            line_height=36,
            style=style,
        )
        event = DanmakuEvent(time=1.0, user="u", text="描边测试")
        return ActiveDanmaku(event=event, layout=builder.build(event), x=0)

    @staticmethod
    def _max_alpha(img):
        return max(
            img.pixelColor(x, y).alpha()
            for y in range(img.height())
            for x in range(img.width())
        )

    def test_outline_alpha_scales_with_text_opacity(self, font_metrics, emoji_cache, font):
        """描边开启 + 文本不透明度 0.5：所有像素 alpha 应 ≤ ~191。

        描边与文本均以 0.5 绘制：纯描边/纯填充像素 alpha≈127；
        两者重叠的 AA 边缘合成 ≈191（0.5+0.5×0.5）。
        若描边独立全不透明，会出现 255 像素。
        """
        dm = self._build_outline_danmaku(font_metrics, emoji_cache, 0.5)
        dm.pre_render(font, emoji_cache)
        assert dm.cached_image is not None
        alphas = [
            dm.cached_image.pixelColor(x, y).alpha()
            for y in range(dm.cached_image.height())
            for x in range(dm.cached_image.width())
            if dm.cached_image.pixelColor(x, y).alpha() > 0
        ]
        assert alphas, "未检测到任何绘制像素"
        assert max(alphas) <= 200, f"出现超过 200 的 alpha（描边未随文本不透明度）: {max(alphas)}"
        assert any(110 <= a <= 145 for a in alphas), "缺少纯 0.5 不透明度像素"

    def test_full_opacity_baseline(self, font_metrics, emoji_cache, font):
        """不透明度 1.0 时整图最大 alpha 应接近 255。"""
        dm = self._build_outline_danmaku(font_metrics, emoji_cache, 1.0)
        dm.pre_render(font, emoji_cache)
        assert dm.cached_image is not None
        assert self._max_alpha(dm.cached_image) > 200


# =============================================================================
# RenderSegment & TextRow
# =============================================================================


class TestDataClasses:
    """测试渲染段落和文本行数据类。"""

    def test_render_segment(self):
        """RenderSegment 应正确存储类型、内容和宽度。"""
        seg = RenderSegment(type='text', content='hello', width=50, color=None)
        assert seg.type == 'text'
        assert seg.content == 'hello'
        assert seg.width == 50
        assert not seg.has_cache

    def test_text_row(self):
        """TextRow 初始状态应为空行。"""
        row = TextRow()
        assert row.segments == []
        assert row.width == 0
