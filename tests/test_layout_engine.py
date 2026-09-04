"""layout_engine.py 单元测试

测试布局引擎的全部静态方法：calculate_params、preload_danmaku_objects、
spawn_new_danmakus、recycle_offscreen。
"""

from dataclasses import FrozenInstanceError

import pytest

from danmakustudio.config import DEFAULT_CONFIG
from danmakustudio.input.event import DanmakuEvent
from danmakustudio.layout.active import ActiveDanmaku
from danmakustudio.layout.engine import LayoutContext, LayoutEngine
from danmakustudio.layout.params import LayerParams
from danmakustudio.render.layout_builder import SHADOW_DX, DanmakuLayoutBuilder

MAX_CONTENT_WIDTH = 800
LINE_HEIGHT = 36  # 与 conftest.py 保持一致，QFontMetrics.height() 实测值


# =============================================================================
# 测试辅助函数
# =============================================================================


def _make_active_danmaku(
    text: str,
    font_metrics,
    emoji_cache: dict,
    *,
    time: float = 1.0,
    max_content_width: int = MAX_CONTENT_WIDTH,
    line_height: int = LINE_HEIGHT,
) -> ActiveDanmaku:
    """创建 ActiveDanmaku 测试实例。"""
    event = DanmakuEvent(time=time, user="用户", text=text)
    builder = DanmakuLayoutBuilder(
        fm=font_metrics,
        emoji_cache=emoji_cache,
        max_content_width=max_content_width,
        line_height=line_height,
        style=DEFAULT_CONFIG.style,
    )
    layout = builder.build(event)
    return ActiveDanmaku(event=event, layout=layout, x=DEFAULT_CONFIG.style.danmaku_x)


# =============================================================================
# calculate_params
# =============================================================================


class TestCalculateParams:
    """测试 calculate_params：文本宽度和渲染层参数计算。"""

    def test_1080p(self):
        text_w, layer = LayoutEngine.calculate_params(1920, 1080)
        cfg = DEFAULT_CONFIG
        assert text_w == int(1920 * cfg.ratio.text_width_ratio)
        assert layer.layer_x == cfg.style.danmaku_x
        assert layer.layer_y == 0
        assert layer.layer_h == 1080 - cfg.ratio.bottom_margin

    def test_720p(self):
        text_w, layer = LayoutEngine.calculate_params(1280, 720)
        cfg = DEFAULT_CONFIG
        assert text_w == int(1280 * cfg.ratio.text_width_ratio)
        assert layer.layer_h == 720 - cfg.ratio.bottom_margin

    def test_layer_params(self):
        _, layer = LayoutEngine.calculate_params(1920, 1080)
        style = DEFAULT_CONFIG.style
        assert isinstance(layer, LayerParams)
        assert layer.layer_x == style.danmaku_x
        assert layer.layer_y == 0
        # 渲染层右缘必须等于视频右缘，弹幕才能从最右端滚入
        assert layer.layer_x + layer.layer_w == 1920

    def test_layer_width_clamped(self):
        style = DEFAULT_CONFIG.style
        tiny_w = style.danmaku_x + 10
        _, layer = LayoutEngine.calculate_params(tiny_w, 1080)
        assert layer.layer_w <= tiny_w - style.danmaku_x

    def test_layer_params_frozen(self):
        _, layer = LayoutEngine.calculate_params(1920, 1080)
        with pytest.raises(FrozenInstanceError):
            layer.layer_w = 0  # type: ignore[misc]


# =============================================================================
# preload_danmaku_objects
# =============================================================================


class TestPreloadDanmakuObjects:
    """测试 preload_danmaku_objects：弹幕对象预创建。"""

    def test_preload_creates_correct_count(self, asset_loader):
        events = [
            DanmakuEvent(time=1.0, user="u1", text="弹幕1"),
            DanmakuEvent(time=2.0, user="u2", text="弹幕2"),
            DanmakuEvent(time=3.0, user="u3", text="弹幕3"),
        ]
        pool = LayoutEngine.preload_danmaku_objects(events, MAX_CONTENT_WIDTH, asset_loader)
        assert len(pool) == 3
        for dm in pool:
            assert isinstance(dm, ActiveDanmaku)

    def test_preload_no_render(self, asset_loader):
        """预创建不应触发预渲染（懒加载）。"""
        events = [DanmakuEvent(time=1.0, user="u", text="测试")]
        pool = LayoutEngine.preload_danmaku_objects(events, MAX_CONTENT_WIDTH, asset_loader)
        assert pool[0].cached_image is None

    def test_preload_empty_events(self, asset_loader):
        pool = LayoutEngine.preload_danmaku_objects([], MAX_CONTENT_WIDTH, asset_loader)
        assert pool == []

    def test_preload_uses_layout_text_w(self, asset_loader):
        events = [DanmakuEvent(time=1.0, user="u", text="测试")]
        pool = LayoutEngine.preload_danmaku_objects(events, 200, asset_loader)
        # 弹幕宽度受 text_w 限制（阴影开启时额外 +SHADOW_DX 预留）
        assert pool[0].total_width <= 200 + SHADOW_DX


# =============================================================================
# recycle_offscreen
# =============================================================================


class TestRecycleOffscreen:
    """测试 recycle_offscreen：完全滚出屏幕左侧的弹幕回收。"""

    def test_in_bounds_danmaku_kept(self, font_metrics, emoji_cache, font):
        dm = _make_active_danmaku("测试", font_metrics, emoji_cache)
        dm.pre_render(font, emoji_cache)
        dm.x = 100
        active = [dm]

        LayoutEngine.recycle_offscreen(active)

        assert len(active) == 1

    def test_offscreen_danmaku_removed(self, font_metrics, emoji_cache, font):
        dm = _make_active_danmaku("测试", font_metrics, emoji_cache)
        dm.pre_render(font, emoji_cache)
        assert dm.cached_image is not None
        dm.x = -(dm.cached_image.width() + 1)  # 完全滚出左边界
        active = [dm]

        LayoutEngine.recycle_offscreen(active)

        assert len(active) == 0
        assert dm.cached_image.isNull()

    def test_mixed_danmakus(self, font_metrics, emoji_cache, font):
        dm_in = _make_active_danmaku("保留", font_metrics, emoji_cache)
        dm_in.pre_render(font, emoji_cache)
        dm_in.x = 100

        dm_out = _make_active_danmaku("移除", font_metrics, emoji_cache)
        dm_out.pre_render(font, emoji_cache)
        dm_out.x = -(dm_out.cached_image.width() + 1)

        active = [dm_in, dm_out]

        LayoutEngine.recycle_offscreen(active)

        assert len(active) == 1
        assert active[0] is dm_in


# =============================================================================
# spawn_new_danmakus
# =============================================================================


class TestSpawnNewDanmakus:
    """测试 spawn_new_danmakus：文本弹幕的发射逻辑。"""

    def test_spawn_at_current_time(self, emoji_cache, asset_loader):
        dm1 = _make_active_danmaku("弹幕1", asset_loader.fm, emoji_cache, time=1.0)
        dm2 = _make_active_danmaku("弹幕2", asset_loader.fm, emoji_cache, time=2.0)
        pool = [dm1, dm2]
        active: list[ActiveDanmaku] = []

        ctx = LayoutContext(event_idx=0)
        emitted = LayoutEngine.spawn_new_danmakus(
            ctx, 1.0, pool, active, asset_loader,
        )

        assert ctx.event_idx == 1
        assert emitted == 1
        assert len(active) == 1
        assert active[0] is dm1
        assert dm1.cached_image is not None

    def test_spawn_sets_lane_and_rightmost_x(self, emoji_cache, asset_loader):
        """发射时应分配车道并置 X 于视频最右端。"""
        dm1 = _make_active_danmaku("弹幕1", asset_loader.fm, emoji_cache, time=1.0)
        pool = [dm1]
        active: list[ActiveDanmaku] = []
        video_width = 1920
        row_height = 38
        max_rows = 4

        ctx = LayoutContext(event_idx=0)
        LayoutEngine.spawn_new_danmakus(
            ctx, 1.0, pool, active, asset_loader,
            max_text_rows=max_rows,
            video_width=video_width,
            row_height=row_height,
        )

        assert dm1.x == video_width
        assert dm1.current_y == dm1.target_y
        assert 0 <= dm1.current_y < row_height * max_rows
        assert dm1.current_y % row_height == 0

    def test_same_time_danmakus_get_distinct_lanes(self, emoji_cache, asset_loader):
        """同一时刻的多条弹幕应分配到不同车道，避免重叠。"""
        dm_list = [
            _make_active_danmaku(f"弹幕{i}", asset_loader.fm, emoji_cache, time=1.0)
            for i in range(8)
        ]
        active: list[ActiveDanmaku] = []

        ctx = LayoutContext(event_idx=0)
        LayoutEngine.spawn_new_danmakus(
            ctx, 1.0, dm_list, active, asset_loader,
            max_text_rows=8, row_height=30,
        )

        assert len(active) == 8
        lanes = {dm.target_y for dm in active}
        assert len(lanes) == 8, f"车道冲突: {lanes}"

    def test_lane_reused_after_clearance(self, emoji_cache, asset_loader):
        """前车已远离后，车道应可被复用。"""
        fillers = [
            _make_active_danmaku(f"占位{i}", asset_loader.fm, emoji_cache, time=0.0)
            for i in range(4)
        ]
        late = _make_active_danmaku("迟到", asset_loader.fm, emoji_cache, time=10.0)
        pool = fillers + [late]
        active: list[ActiveDanmaku] = []

        ctx = LayoutContext(event_idx=0)
        LayoutEngine.spawn_new_danmakus(
            ctx, 0.0, pool, active, asset_loader,
            max_text_rows=4, row_height=30,
        )
        assert len(active) == 4

        emitted = LayoutEngine.spawn_new_danmakus(
            ctx, 10.0, pool, active, asset_loader,
            max_text_rows=4, row_height=30,
        )
        assert emitted == 1
        assert 0 <= late.target_y < 4 * 30

    def test_spawn_respects_text_top(self, emoji_cache, asset_loader):
        """车道应从顶部边距之下开始分配，不贴画面上边缘。"""
        dm1 = _make_active_danmaku("弹幕1", asset_loader.fm, emoji_cache, time=1.0)
        pool = [dm1]
        active: list[ActiveDanmaku] = []

        ctx = LayoutContext(event_idx=0)
        LayoutEngine.spawn_new_danmakus(
            ctx, 1.0, pool, active, asset_loader,
            max_text_rows=4, video_width=1920, row_height=30, text_top=12,
        )

        assert dm1.current_y == dm1.target_y
        assert 12 <= dm1.current_y < 12 + 4 * 30
        assert (dm1.current_y - 12) % 30 == 0

    def test_no_spawn_before_time(self, emoji_cache, asset_loader):
        dm1 = _make_active_danmaku("弹幕1", asset_loader.fm, emoji_cache, time=5.0)
        pool = [dm1]
        active: list[ActiveDanmaku] = []

        ctx = LayoutContext(event_idx=0)
        emitted = LayoutEngine.spawn_new_danmakus(
            ctx, 1.0, pool, active, asset_loader,
        )

        assert ctx.event_idx == 0
        assert emitted == 0
        assert len(active) == 0


    def test_spawn_emits_all_pending(self, emoji_cache, asset_loader):
        """时间已到的弹幕应一次性全部发射（无批次节流）。"""
        dm_list = [
            _make_active_danmaku(f"弹幕{i}", asset_loader.fm, emoji_cache, time=1.0)
            for i in range(5)
        ]
        active: list[ActiveDanmaku] = []

        ctx = LayoutContext(event_idx=0)
        emitted = LayoutEngine.spawn_new_danmakus(
            ctx, 1.0, dm_list, active, asset_loader,
        )

        assert emitted == 5
        assert ctx.event_idx == 5
        assert len(active) == 5

    def test_spawn_respects_text_row_budget(self, emoji_cache, asset_loader):
        """单行模式下每条弹幕占 1 行，max_text_rows 限制单帧发射数量。"""
        dm_list = [
            _make_active_danmaku(f"弹幕{i}", asset_loader.fm, emoji_cache, time=1.0)
            for i in range(6)
        ]
        active: list[ActiveDanmaku] = []
        ctx = LayoutContext(event_idx=0)

        emitted = LayoutEngine.spawn_new_danmakus(
            ctx,
            1.0,
            dm_list,
            active,
            asset_loader,
            max_text_rows=3,
        )

        assert emitted == 3
        assert len(active) == 3
        assert ctx.event_idx == 3

    def test_row_budget_does_not_deadlock_when_screen_is_full(
        self, emoji_cache, asset_loader,
    ):
        """行数预算不能用当前活跃弹幕做硬限制，否则文本流会卡死。"""
        active = [
            _make_active_danmaku(
                f"已有{i}",
                asset_loader.fm,
                emoji_cache,
                time=0.0,
            )
            for i in range(DEFAULT_CONFIG.ratio.max_text_rows)
        ]

        next_dm = _make_active_danmaku(
            "新弹幕",
            asset_loader.fm,
            emoji_cache,
            time=1.0,
        )
        pool = [next_dm]
        ctx = LayoutContext(event_idx=0)

        emitted = LayoutEngine.spawn_new_danmakus(
            ctx,
            1.0,
            pool,
            active,
            asset_loader,
            max_text_rows=DEFAULT_CONFIG.ratio.max_text_rows,
        )

        assert emitted == 1
        assert ctx.event_idx == 1
        assert active[-1] is next_dm

    def test_already_rendered_not_re_rendered(self, emoji_cache, font, asset_loader):
        dm1 = _make_active_danmaku("弹幕1", asset_loader.fm, emoji_cache, time=1.0)
        dm1.pre_render(font, emoji_cache)
        original_image = dm1.cached_image
        pool = [dm1]
        active: list[ActiveDanmaku] = []

        ctx = LayoutContext(event_idx=0)
        LayoutEngine.spawn_new_danmakus(
            ctx, 1.0, pool, active, asset_loader,
        )

        assert dm1.cached_image is original_image

    def test_spawn_no_interval_throttle(self, emoji_cache, asset_loader):
        """弹幕应在自身时间点立即发射，不受发射间隔限制。"""
        dm1 = _make_active_danmaku("弹幕1", asset_loader.fm, emoji_cache, time=1.0)
        dm2 = _make_active_danmaku("弹幕2", asset_loader.fm, emoji_cache, time=1.1)
        pool = [dm1, dm2]
        active: list[ActiveDanmaku] = []

        ctx = LayoutContext(event_idx=0)
        LayoutEngine.spawn_new_danmakus(ctx, 1.0, pool, active, asset_loader)
        assert len(active) == 1

        # 0.1 秒后（远小于旧实现的 0.5 秒间隔）第二条应立即发射
        emitted = LayoutEngine.spawn_new_danmakus(ctx, 1.1, pool, active, asset_loader)
        assert emitted == 1
        assert len(active) == 2
