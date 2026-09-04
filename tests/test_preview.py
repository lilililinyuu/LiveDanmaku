"""预览图相关单元测试。"""

import pytest

from danmakustudio.config import DEFAULT_CONFIG
from danmakustudio.core.burner import danmaku_layout_context, danmaku_state_at
from danmakustudio.core.preview import default_preview_times
from danmakustudio.input.event import DanmakuEvent
from danmakustudio.layout.active import ActiveDanmaku
from danmakustudio.layout.engine import LayoutContext
from danmakustudio.render.layout_builder import DanmakuLayoutBuilder


def _make_danmaku(text, font_metrics, emoji_cache, time=1.0):
    builder = DanmakuLayoutBuilder(
        fm=font_metrics,
        emoji_cache=emoji_cache,
        max_content_width=800,
        line_height=36,
        style=DEFAULT_CONFIG.style,
    )
    event = DanmakuEvent(time=time, user="u", text=text)
    return ActiveDanmaku(event=event, layout=builder.build(event), x=0)


def test_default_preview_times_with_events():
    events = [
        DanmakuEvent(time=5.0, user="a", text="x"),
        DanmakuEvent(time=20.0, user="b", text="x"),
        DanmakuEvent(time=60.0, user="c", text="x"),
    ]
    assert default_preview_times(events, 100.0) == [5.0, 20.0, 60.0]


def test_default_preview_times_no_events():
    assert default_preview_times([], 100.0) == [50.0]
    assert default_preview_times([], 0.0) == [0.0]


def test_layout_context_matches_burner_formula():
    class _A:
        line_height = 36

    row_height, top_margin, effective_rows = danmaku_layout_context(
        DEFAULT_CONFIG, _A(), 1920, 1080,
    )
    ratio = DEFAULT_CONFIG.ratio
    style = DEFAULT_CONFIG.style
    assert row_height == 36 + style.row_vertical_gap
    assert top_margin == ratio.top_margin
    visible = 1080 - ratio.top_margin - ratio.bottom_margin
    assert effective_rows == max(1, min(ratio.max_text_rows, visible // row_height))


def test_state_at_matches_scroll_formula(font_metrics, emoji_cache, asset_loader):
    """t 时刻的活跃弹幕位置应符合 video_width - 300*(t - 出生时刻)。"""
    dm1 = _make_danmaku("弹幕1", font_metrics, emoji_cache, time=1.0)
    dm2 = _make_danmaku("弹幕2", font_metrics, emoji_cache, time=5.0)
    pool = [dm1, dm2]
    ctx = LayoutContext(event_idx=0)

    active = danmaku_state_at(
        pool, 2.0, 30.0, ctx, asset_loader,
        max_text_rows=4, video_width=640, row_height=30, text_top=12,
        scroll_speed=300.0,
    )

    assert ctx.event_idx == 1
    assert [d for d in active] == [dm1]
    assert dm1.x == pytest.approx(640 - 300 * (2.0 - 1.0))
    assert dm1.cached_image is not None


def test_state_at_drops_recycled(font_metrics, emoji_cache, asset_loader):
    dm1 = _make_danmaku("弹幕1", font_metrics, emoji_cache, time=0.0)
    pool = [dm1]
    ctx = LayoutContext(event_idx=0)

    active = danmaku_state_at(
        pool, 10.0, 30.0, ctx, asset_loader,
        max_text_rows=4, video_width=640, row_height=30, text_top=12,
        scroll_speed=300.0,
    )

    assert len(active) == 0
    assert ctx.event_idx == 1
