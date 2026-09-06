"""弹幕渲染器模块"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter


class DanmakuRenderer:
    def __init__(self, layer_params, scale: float = 1.0):
        self._layer_params = layer_params
        self._scale = max(1.0, float(scale))
        self.canvas = QImage(
            max(1, round(layer_params.layer_w * self._scale)),
            max(1, round(layer_params.layer_h * self._scale)),
            QImage.Format.Format_ARGB32,
        )
        self.painter = QPainter()
        self.painter.begin(self.canvas)
        # 超采样：逻辑坐标绘制到高分辨率画布，文字/表情边缘更细腻
        self.painter.scale(self._scale, self._scale)
        self.painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self.painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    def render_frame(self, active_danmakus):
        layer_y = self._layer_params.layer_y
        self.canvas.fill(Qt.GlobalColor.transparent)

        for dm in active_danmakus:
            # 透明度已在缓存图片中设置，此处不再调整
            local_x = dm.x - self._layer_params.layer_x
            local_y = int(dm.current_y) - layer_y
            dm.render(self.painter, int(local_x), local_y)

    def get_frame_data(self):
        return memoryview(self.canvas.bits())

    def end(self):
        self.painter.end()
