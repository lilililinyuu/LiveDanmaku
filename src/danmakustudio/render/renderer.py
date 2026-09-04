"""弹幕渲染器模块"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter


class DanmakuRenderer:
    def __init__(self, layer_params):
        self._layer_params = layer_params
        self.canvas = QImage(layer_params.layer_w, layer_params.layer_h, QImage.Format.Format_ARGB32)
        self.painter = QPainter()
        self.painter.begin(self.canvas)
        self.painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

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
