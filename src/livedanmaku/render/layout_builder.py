"""弹幕布局构建器"""

from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
)

from ..config.models import DEFAULT_CONFIG
from .segments import RenderSegment, TextRow

# 文本阴影偏移量（像素），阴影颜色与不透明度可在配置中调整
SHADOW_DX = 2
SHADOW_DY = 2


def _strip_trailing_spacing(row):
    while row.segments and row.segments[-1].type == 'spacing':
        removed = row.segments.pop()
        row.width -= removed.width


class DanmakuLayout:
    __slots__ = ['cached_image', 'emoji_style', 'height', 'line_height',
                 'multiline_row_gap', 'rows', 'text_ascent', 'text_style',
                 'total_width', 'vertical_padding']

    def __init__(self, rows, total_width, height,
                 line_height, multiline_row_gap, text_ascent, vertical_padding,
                 text_style, emoji_style):
        self.rows = rows
        self.total_width = total_width
        self.height = height
        self.line_height = line_height
        self.multiline_row_gap = multiline_row_gap
        self.text_ascent = text_ascent
        self.vertical_padding = vertical_padding
        self.cached_image = None
        self.text_style = text_style
        self.emoji_style = emoji_style

    def pre_render(self, font, emoji_cache, scale: float = 1.0):
        """按 scale 超采样预渲染（默认 1.0 = 与视频同分辨率）。"""
        s = max(1.0, float(scale))
        self.cached_image = QImage(
            max(1, round(self.total_width * s)),
            max(1, round(self.height * s)),
            QImage.Format.Format_ARGB32,
        )
        self.cached_image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(self.cached_image)
        painter.scale(s, s)
        painter.setFont(font)

        ts = self.text_style
        use_outline = (ts.outline_width > 0 and bool(ts.outline_color))
        use_shadow = (bool(ts.shadow_color) and ts.shadow_opacity > 0)
        outline_color = QColor(ts.outline_color) if use_outline else None
        shadow_color = QColor(ts.shadow_color) if use_shadow else None

        painter.setOpacity(ts.opacity)

        for r_idx, row in enumerate(self.rows):
            curr_row_top = r_idx * (self.line_height + self.multiline_row_gap)
            text_baseline_y = curr_row_top + self.vertical_padding + self.text_ascent
            curr_x = 0
            for seg in row.segments:
                if seg.type == 'text':
                    if use_outline and outline_color:
                        path = QPainterPath()
                        path.addText(curr_x, text_baseline_y, font, seg.content)
                        if use_shadow and shadow_color:
                            painter.save()
                            painter.translate(SHADOW_DX, SHADOW_DY)
                            painter.setOpacity(ts.shadow_opacity)
                            painter.setPen(QPen(shadow_color, ts.outline_width))
                            painter.setBrush(QBrush(shadow_color))
                            painter.drawPath(path)
                            painter.restore()
                        painter.setPen(QPen(outline_color, ts.outline_width))
                        painter.setBrush(QBrush(seg.color))
                        painter.drawPath(path)
                    else:
                        if use_shadow and shadow_color:
                            painter.save()
                            painter.translate(SHADOW_DX, SHADOW_DY)
                            painter.setOpacity(ts.shadow_opacity)
                            painter.setPen(shadow_color)
                            painter.drawText(curr_x, text_baseline_y, seg.content)
                            painter.restore()
                        painter.setPen(seg.color)
                        painter.drawText(curr_x, text_baseline_y, seg.content)
                elif seg.type == 'emoji' and seg.has_cache:
                    scaled_img = emoji_cache[seg.content]
                    # 表情按逻辑行高框定目标矩形，源图为倍率分辨率（1:1 像素映射更清晰）
                    logical_h = scaled_img.height() / s if s else self.line_height
                    emoji_y = curr_row_top + (self.line_height - logical_h) / 2
                    painter.save()
                    painter.setOpacity(self.emoji_style.opacity)
                    painter.drawImage(
                        QRectF(curr_x, emoji_y, logical_h, logical_h),
                        scaled_img,
                    )
                    painter.restore()
                curr_x += seg.width
        painter.end()

    def render(self, painter, x, y):
        if self.cached_image:
            # 目标矩形 = 逻辑尺寸：超采样缓存按原比例缩放合成，清晰度不损失
            painter.drawImage(
                QRectF(x, y, self.total_width, self.height), self.cached_image
            )


class DanmakuLayoutBuilder:
    def __init__(self, fm, emoji_cache, max_content_width, line_height, style=DEFAULT_CONFIG.style):
        self.fm = fm
        self.emoji_cache = emoji_cache
        # 单行滚动模式：不折行，max_content_width 仅保留用于接口兼容
        self.max_content_width = max_content_width
        self.line_height = line_height
        self.style = style

    def build(self, event):
        text_style = self.style.member_text if event.is_membership else self.style.text
        raw_segments = self._build_text_segments(event, text_style)

        # 单行：整条弹幕作为一行，随滚动完整展示全部文本
        row = TextRow()
        total_width = 0
        for seg in raw_segments:
            row.segments.append(seg)
            total_width += seg.width
        row.width = total_width
        _strip_trailing_spacing(row)
        rows = [row] if row.segments else []
        return self._calc_dimensions(rows, row.width, text_style)

    def _build_text_segments(self, event, text_style):
        raw_segments = []
        text = event.text
        i = 0
        buffer = []
        text_color = QColor(text_style.color)

        def flush():
            if buffer:
                merged = ''.join(buffer)
                raw_segments.append(RenderSegment('text', merged, self.fm.horizontalAdvance(merged), text_color))
                buffer.clear()

        while i < len(text):
            if text[i] == '[':
                end = text.find(']', i)
                if end != -1:
                    name = text[i+1:end]
                    if name in self.emoji_cache:
                        flush()
                        if raw_segments and raw_segments[-1].type != 'spacing':
                            raw_segments.append(RenderSegment('spacing', '', self.style.emoji.spacing))
                        raw_segments.append(RenderSegment('emoji', name, self.emoji_cache[name].width(), has_cache=True))
                        i = end + 1
                        continue
            buffer.append(text[i])
            i += 1
        flush()
        return raw_segments

    def _calc_dimensions(self, rows, max_row_width_seen, text_style):
        # 阴影开启时给缓存图预留偏移空间，避免下沉字母（如 y）的阴影被边缘截断
        use_shadow = bool(text_style.shadow_color) and text_style.shadow_opacity > 0
        total_width = max_row_width_seen + (SHADOW_DX if use_shadow else 0)
        num_rows = len(rows)
        height = (
            num_rows * self.line_height
            + (num_rows - 1) * self.style.multiline_row_gap
            + (SHADOW_DY if use_shadow else 0)
        )
        text_ascent = self.fm.ascent()
        text_descent = self.fm.descent()
        vertical_padding = (self.line_height - text_ascent - text_descent) // 2
        return DanmakuLayout(rows, total_width, height,
                             self.line_height, self.style.multiline_row_gap,
                             text_ascent, vertical_padding,
                             text_style, self.style.emoji)
