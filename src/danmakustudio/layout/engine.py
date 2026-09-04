"""布局引擎"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtGui import QImage

from ..config.models import DEFAULT_CONFIG
from .params import LayerParams


@dataclass
class LayoutContext:
    event_idx: int = 0
    # 车道占用表：row -> (最近一条弹幕的出生时间, 其宽度)
    # 用于出生时避开会与前车重叠的车道
    lane_last: dict[int, tuple[float, float]] = field(default_factory=dict)

class LayoutEngine:
    @staticmethod
    def calculate_params(w, h, style=DEFAULT_CONFIG.style, ratio=DEFAULT_CONFIG.ratio):
        text_w = int(w * ratio.text_width_ratio)
        layer_x = style.danmaku_x
        # 渲染层必须延伸到视频最右端，弹幕才能从最右端滚入画面
        layer_w = max(0, w - layer_x)
        layer_y = 0
        layer_h = h - ratio.bottom_margin
        layer_params = LayerParams(layer_x=layer_x, layer_y=layer_y, layer_w=layer_w, layer_h=layer_h)
        return text_w, layer_params

    @staticmethod
    def preload_danmaku_objects(events, text_w, asset_provider, style=DEFAULT_CONFIG.style):
        from ..render.layout_builder import DanmakuLayoutBuilder
        from .active import ActiveDanmaku
        builder = DanmakuLayoutBuilder(
            fm=asset_provider.fm,
            emoji_cache=asset_provider.emoji_cache,
            max_content_width=text_w,
            line_height=asset_provider.line_height,
            style=style,
        )
        pool = []
        for event in events:
            layout = builder.build(event)
            dm = ActiveDanmaku(event=event, layout=layout, x=style.danmaku_x)
            pool.append(dm)
        return pool

    @staticmethod
    def spawn_new_danmakus(
        ctx, current_time, danmaku_pool, active,
        asset_provider, max_text_rows=None,
        video_width=1920, row_height=30, text_top=0,
        scroll_speed=300.0,
    ):
        """发射所有时间已到的事件（无间隔/批次节流），仅受行数预算限制。

        车道分配带防重叠探测：选择与前车出生间距足够（间距≥前车宽度）的
        空闲车道；全部忙碌时退回哈希车道（保证按原时间发射）。
        行数预算不足时事件留到下一帧发射（最多延迟一帧）。
        """
        event_idx = ctx.event_idx
        pending_end = event_idx
        for i in range(event_idx, len(danmaku_pool)):
            if danmaku_pool[i].event.time > current_time:
                break
            pending_end = i + 1

        text_emitted = 0
        emitted_text_rows = 0

        while event_idx < pending_end:
            dm = danmaku_pool[event_idx]
            dm_rows = _danmaku_row_count(dm)
            if not _fits_row_budget(emitted_text_rows, dm_rows, max_text_rows):
                break
            text_emitted += 1
            emitted_text_rows += dm_rows

            if dm.cached_image is None:
                dm.pre_render(asset_provider.font, asset_provider.emoji_cache)
            if max_text_rows is None:
                max_text_rows = 10
            row = LayoutEngine.pick_lane(
                ctx, current_time, dm, max_text_rows, scroll_speed,
            )
            dm.target_y = text_top + row * row_height
            dm.current_y = dm.target_y
            dm.x = video_width

            active.append(dm)

            event_idx += 1

        ctx.event_idx = event_idx

        return text_emitted

    @staticmethod
    def recycle_offscreen(active_danmakus):
        """回收已完全滚出屏幕左侧的弹幕，并释放其缓存图片。"""
        remaining = []
        for dm in active_danmakus:
            if dm.x + dm.cached_image.width() < 0:
                dm.cached_image = QImage()
                continue
            remaining.append(dm)
        active_danmakus[:] = remaining

    @staticmethod
    def pick_lane(ctx, current_time, dm, max_rows, scroll_speed):
        """为弹幕选择不重叠的车道。

        弹幕同速滚动，同车道内相对位置不变：只要出生间距（像素）≥ 前车宽度，
        全程都不会重叠。多行弹幕占用连续多车道。
        """
        n_rows = _danmaku_row_count(dm)
        width = float(dm.total_width)
        start = hash(dm.event.text + str(dm.event.time)) % max_rows

        def _lane_blocked(row: int) -> bool:
            last = ctx.lane_last.get(row)
            if last is None:
                return False
            last_time, last_width = last
            return (current_time - last_time) * scroll_speed < last_width

        # 从哈希车道开始探测空闲车道
        for offset in range(max_rows):
            row = (start + offset) % max_rows
            if row + n_rows > max_rows:
                continue
            if any(_lane_blocked(r) for r in range(row, row + n_rows)):
                continue
            for r in range(row, row + n_rows):
                ctx.lane_last[r] = (current_time, width)
            return row

        # 全部忙碌：退回哈希车道，保证按原时间发射
        row = min(start, max(0, max_rows - n_rows))
        for r in range(row, row + n_rows):
            ctx.lane_last[r] = (current_time, width)
        return row

def _danmaku_row_count(dm):
    return max(1, len(dm.rows))

def _fits_row_budget(used_rows, new_rows, max_rows):
    if max_rows is None:
        return True
    if max_rows <= 0:
        return False
    if used_rows == 0:
        return True
    return used_rows + new_rows <= max_rows
