"""渲染器测试。

验证 DanmakuRenderer 将预渲染的弹幕缓存图绘制到图层画布的
坐标换算、逐帧清屏、越界裁剪与帧数据输出。
"""

from danmakustudio.config import DEFAULT_CONFIG
from danmakustudio.input.event import DanmakuEvent
from danmakustudio.layout.active import ActiveDanmaku
from danmakustudio.layout.params import LayerParams
from danmakustudio.render.layout_builder import DanmakuLayoutBuilder
from danmakustudio.render.renderer import DanmakuRenderer

LAYER = LayerParams(layer_x=10, layer_y=0, layer_w=400, layer_h=300)


def _make_danmaku(text, font_metrics, emoji_cache):
    event = DanmakuEvent(time=1.0, user="u", text=text)
    builder = DanmakuLayoutBuilder(
        fm=font_metrics,
        emoji_cache=emoji_cache,
        max_content_width=300,
        line_height=36,
        style=DEFAULT_CONFIG.style,
    )
    return ActiveDanmaku(event=event, layout=builder.build(event), x=LAYER.layer_x)


def _region_has_alpha(canvas, x0, y0, x1, y1):
    """判断画布指定区域内是否存在非透明像素。"""
    for y in range(max(0, y0), min(canvas.height(), y1)):
        for x in range(max(0, x0), min(canvas.width(), x1)):
            if canvas.pixelColor(x, y).alpha() > 0:
                return True
    return False


class TestDanmakuRenderer:
    def test_render_frame_draws_danmaku(self, font_metrics, emoji_cache, font):
        """弹幕应绘制在 (x - layer_x, int(current_y) - layer_y) 处。"""
        dm = _make_danmaku("测试弹幕", font_metrics, emoji_cache)
        dm.pre_render(font, emoji_cache)
        assert dm.cached_image is not None
        image = dm.cached_image
        dm.current_y = 50.0
        dm.x = 100
        renderer = DanmakuRenderer(LAYER)
        try:
            renderer.render_frame([dm])

            local_x = dm.x - LAYER.layer_x
            local_y = int(dm.current_y) - LAYER.layer_y
            assert _region_has_alpha(
                renderer.canvas,
                local_x, local_y,
                local_x + image.width(), local_y + image.height(),
            )
            # 弹幕区域之外应为透明
            assert not _region_has_alpha(
                renderer.canvas,
                local_x + image.width() + 1, local_y,
                LAYER.layer_w, local_y + image.height(),
            )
        finally:
            renderer.end()

    def test_render_frame_empty_is_transparent(self):
        """空列表渲染后画布应全透明（逐帧清屏）。"""
        renderer = DanmakuRenderer(LAYER)
        try:
            renderer.render_frame([])
            assert not _region_has_alpha(renderer.canvas, 0, 0, LAYER.layer_w, LAYER.layer_h)
        finally:
            renderer.end()

    def test_render_frame_multiple_danmakus(self, font_metrics, emoji_cache, font):
        """多条弹幕应同时可见。"""
        dm1 = _make_danmaku("第一条", font_metrics, emoji_cache)
        dm2 = _make_danmaku("第二条", font_metrics, emoji_cache)
        dm1.pre_render(font, emoji_cache)
        dm2.pre_render(font, emoji_cache)
        dm1.x, dm1.current_y = 100, 50.0
        dm2.x, dm2.current_y = 200, 120.0
        renderer = DanmakuRenderer(LAYER)
        try:
            renderer.render_frame([dm1, dm2])

            for dm in (dm1, dm2):
                assert dm.cached_image is not None
                image = dm.cached_image
                local_x = dm.x - LAYER.layer_x
                local_y = int(dm.current_y) - LAYER.layer_y
                assert _region_has_alpha(
                    renderer.canvas,
                    local_x, local_y,
                    local_x + image.width(), local_y + image.height(),
                )
        finally:
            renderer.end()

    def test_offscreen_right_is_clipped(self, font_metrics, emoji_cache, font):
        """X 位于渲染层右缘之外的弹幕应被裁剪（最右端出现语义）。"""
        dm = _make_danmaku("测试弹幕", font_metrics, emoji_cache)
        dm.pre_render(font, emoji_cache)
        dm.current_y = 50.0
        dm.x = LAYER.layer_x + LAYER.layer_w
        renderer = DanmakuRenderer(LAYER)
        try:
            renderer.render_frame([dm])
            assert not _region_has_alpha(renderer.canvas, 0, 0, LAYER.layer_w, LAYER.layer_h)
        finally:
            renderer.end()

    def test_get_frame_data_size(self):
        """帧数据应为 layer_w * layer_h * 4 字节的 memoryview。"""
        renderer = DanmakuRenderer(LAYER)
        try:
            data = renderer.get_frame_data()
            assert isinstance(data, memoryview)
            assert len(data) == LAYER.layer_w * LAYER.layer_h * 4
        finally:
            renderer.end()
