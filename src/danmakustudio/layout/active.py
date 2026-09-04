"""活跃弹幕节点

存储当前屏幕上一条弹幕的运行时状态，由 LayoutEngine 管理生命周期。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QImage, QPainter

if TYPE_CHECKING:
    from ..input.event import DanmakuEvent
    from ..render.layout_builder import DanmakuLayout
    from ..render.segments import TextRow


class ActiveDanmaku:
    """活跃弹幕节点：存储当前屏幕上一条弹幕的运行时状态。

    职责：
        - 持有弹幕事件和布局数据
        - 管理位置状态（current_y、x）
        - 委托渲染给 DanmakuLayout

    段落解析、折行、尺寸计算、预渲染等构造阶段逻辑已提取至
    DanmakuLayoutBuilder 和 DanmakuLayout。

    使用 __slots__ 而非 __dict__ 以节省内存。
    """

    __slots__ = ['current_y', 'event', 'layout', 'target_y', 'x']

    def __init__(
        self,
        event: DanmakuEvent,
        layout: DanmakuLayout,
        x: int = 0,
    ):
        """初始化活跃弹幕节点。

        Args:
            event: 弹幕事件数据
            layout: 预构建的弹幕布局（由 DanmakuLayoutBuilder 生成）
            x: 弹幕/渲染层 X 偏移
        """
        self.event = event
        self.layout = layout
        self.current_y = 0.0
        self.target_y = 0.0
        self.x = x

    # -------------------------------------------------------------------------
    # 属性代理：委托给 DanmakuLayout
    # -------------------------------------------------------------------------

    @property
    def height(self) -> int:
        return self.layout.height

    @property
    def rows(self) -> list[TextRow]:
        return self.layout.rows

    @property
    def total_width(self) -> int:
        return self.layout.total_width

    @property
    def cached_image(self) -> QImage | None:
        return self.layout.cached_image

    @cached_image.setter
    def cached_image(self, value: QImage | None) -> None:
        self.layout.cached_image = value

    # -------------------------------------------------------------------------
    # 渲染委托
    # -------------------------------------------------------------------------

    def pre_render(
        self,
        font,
        emoji_cache: dict[str, QImage],
    ) -> None:
        """预渲染弹幕到缓存图片，委托给 DanmakuLayout。"""
        self.layout.pre_render(font, emoji_cache)

    def render(self, painter: QPainter, x: int, y: int) -> None:
        """使用缓存的预渲染图片绘制弹幕，委托给 DanmakuLayout。"""
        self.layout.render(painter, x, y)
