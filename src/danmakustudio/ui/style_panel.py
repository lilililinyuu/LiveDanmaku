"""GUI 样式设置面板。

把样式配置暴露为可视化控件；勾选"使用自定义样式"后，
面板值通过 StyleOverrides 覆盖配置文件。
"""

from __future__ import annotations

import json
from dataclasses import asdict

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QImage,
    QPainter,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..config.models import (
    DEFAULT_CONFIG,
    DanmakuConfig,
    EmojiStyle,
    LayoutStyle,
    StyleOverrides,
    TextStyle,
)
from ..input.event import DanmakuEvent
from ..render.assets import _RESOURCE_ROOT
from ..render.layout_builder import DanmakuLayoutBuilder
from ._shared import gui_style_path
from .theme import CheckMarkCheckBox


def _opacity_spin() -> QDoubleSpinBox:
    """0~1 步进 0.05 的不透明度输入框。"""
    spin = QDoubleSpinBox()
    spin.setRange(0.0, 1.0)
    spin.setSingleStep(0.05)
    spin.setDecimals(2)
    spin.setValue(1.0)
    # QSS 的 min-height 不参与布局计算，需程序化设置以避免行重叠
    spin.setMinimumHeight(38)
    spin.setFixedWidth(70)
    return spin


class ColorButton(QPushButton):
    """色块按钮：点击弹出取色对话框；None 表示"无"（禁用对应效果）。

    内部保存原始十六进制字符串，保证与配置文件的写法完全一致
    （QColor.name() 会把 "#FFFFFF" 输出为小写 "#ffffff"）。
    """

    changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._hex: str | None = None
        self.setFixedSize(48, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("点击选择颜色")
        self.clicked.connect(self._pick_color)
        self._refresh()

    def _pick_color(self) -> None:
        initial = QColor(self._hex) if self._hex else QColor("#FFFFFF")
        color = QColorDialog.getColor(initial, self, "选择颜色")
        if color.isValid():
            self.set_color_hex(color.name())

    def set_color_hex(self, hex_str: str | None) -> None:
        self._hex = hex_str
        self._refresh()
        self.changed.emit()

    def color_hex(self) -> str | None:
        return self._hex

    def _refresh(self) -> None:
        if self._hex is None:
            self.setText("无")
            self.setStyleSheet(
                "QPushButton { background-color: #f0f2f6; border: 1px solid #c7cfdb;"
                " border-radius: 5px; color: #6b7280; font-size: 9pt; }"
            )
        else:
            self.setText("")
            self.setStyleSheet(
                f"QPushButton {{ background-color: {self._hex};"
                f" border: 1px solid #c7cfdb; border-radius: 5px; }}"
            )


def _bare_check(tooltip: str) -> QCheckBox:
    """无文字的纯打勾开关（带主题勾号绘制）。"""
    check = CheckMarkCheckBox()
    check.setToolTip(tooltip)
    check.setFixedWidth(22)
    return check


class TextStyleControls(QWidget):
    """一组 TextStyle 的编辑控件（普通/会员页共用）。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        color_row_extra: QWidget | None = None,
    ):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        layout = QFormLayout()
        layout.setContentsMargins(14, 14, 14, 8)
        layout.setVerticalSpacing(16)
        layout.setHorizontalSpacing(14)

        text_row = QHBoxLayout()
        text_row.setSpacing(8)
        self.color_btn = ColorButton()
        self.opacity_spin = _opacity_spin()
        self.opacity_spin.setToolTip("文本整体不透明度（描边随之一起变化）")
        text_row.addWidget(self.color_btn)
        text_row.addWidget(QLabel("不透明度"))
        text_row.addWidget(self.opacity_spin)
        text_row.addStretch(1)
        if color_row_extra is not None:
            text_row.addWidget(color_row_extra)
        layout.addRow("颜色", text_row)

        outline_row = QHBoxLayout()
        outline_row.setSpacing(8)
        self.outline_check = _bare_check("勾选后为文本描边")
        self.outline_color_btn = ColorButton()
        self.outline_width_spin = QDoubleSpinBox()
        self.outline_width_spin.setRange(0.0, 5.0)
        self.outline_width_spin.setSingleStep(0.1)
        self.outline_width_spin.setDecimals(1)
        self.outline_width_spin.setValue(0.7)
        self.outline_width_spin.setMinimumHeight(38)
        self.outline_width_spin.setFixedWidth(70)
        self.outline_width_spin.setToolTip("描边厚度（像素）")
        outline_row.addWidget(self.outline_check)
        outline_row.addWidget(self.outline_color_btn)
        outline_row.addWidget(QLabel("厚度"))
        outline_row.addWidget(self.outline_width_spin)
        outline_row.addStretch(1)
        layout.addRow("描边", outline_row)

        shadow_row = QHBoxLayout()
        shadow_row.setSpacing(8)
        self.shadow_check = _bare_check("勾选后为文本阴影")
        self.shadow_color_btn = ColorButton()
        self.shadow_opacity_spin = _opacity_spin()
        self.shadow_opacity_spin.setToolTip("阴影不透明度（独立于文本不透明度）")
        shadow_row.addWidget(self.shadow_check)
        shadow_row.addWidget(self.shadow_color_btn)
        shadow_row.addWidget(QLabel("不透明度"))
        shadow_row.addWidget(self.shadow_opacity_spin)
        shadow_row.addStretch(1)
        layout.addRow("阴影", shadow_row)

        outer.addLayout(layout)

    def load_from(self, ts: TextStyle) -> None:
        """按给定样式填充控件。"""
        self.color_btn.set_color_hex(ts.color or None)
        self.opacity_spin.setValue(ts.opacity)
        self.outline_check.setChecked(bool(ts.outline_color) and ts.outline_width > 0)
        self.outline_color_btn.set_color_hex(ts.outline_color or None)
        self.outline_width_spin.setValue(ts.outline_width)
        self.shadow_check.setChecked(bool(ts.shadow_color) and ts.shadow_opacity > 0)
        self.shadow_color_btn.set_color_hex(ts.shadow_color or None)
        self.shadow_opacity_spin.setValue(ts.shadow_opacity)

    def text_style(self) -> TextStyle:
        """由当前控件值构造 TextStyle。"""
        outline_hex = self.outline_color_btn.color_hex() or ""
        if self.outline_check.isChecked():
            outline_width = self.outline_width_spin.value()
        else:
            outline_hex = ""
            outline_width = 0.0
        shadow_hex = self.shadow_color_btn.color_hex() or ""
        if self.shadow_check.isChecked():
            shadow_opacity = self.shadow_opacity_spin.value()
        else:
            shadow_hex = ""
            shadow_opacity = 0.0
        return TextStyle(
            color=self.color_btn.color_hex() or "#FFFFFF",
            opacity=self.opacity_spin.value(),
            outline_color=outline_hex,
            outline_width=outline_width,
            shadow_color=shadow_hex,
            shadow_opacity=shadow_opacity,
        )


class StylePreview(QWidget):
    """实时样式预览：用面板当前样式渲染示例弹幕。"""

    DEFAULT_BG = "#808080"
    # 预览用表情（取自 assets/emoji），连续 3 个以展示大小与间距效果
    EMOJI_NAMES = ("_LOVEラブ", "_nagi凪", "_ペンライト")

    def __init__(self, panel: StylePanel, parent: QWidget | None = None):
        super().__init__(parent)
        self._panel = panel
        self._bg = QColor(self.DEFAULT_BG)
        self._scale = 1.0
        self.setObjectName("stylePreview")
        self.setMinimumHeight(170)
        self._emoji_images = self._load_emoji_images()

    def set_render_scale(self, scale: float) -> None:
        """设置渲染超采样倍率并重绘。"""
        self._scale = max(1.0, float(scale))
        self.update()

    def render_scale(self) -> float:
        return self._scale

    def _load_emoji_images(self) -> dict[str, QImage]:
        """加载预览用本地表情原图；缺失时忽略，对应文字按原样显示。"""
        images: dict[str, QImage] = {}
        emoji_dir = _RESOURCE_ROOT / "assets" / "emoji"
        for name in self.EMOJI_NAMES:
            img = QImage(str(emoji_dir / f"{name}.png"))
            if not img.isNull():
                images[name] = img
        return images

    def set_bg_color(self, hex_str: str | None) -> None:
        """设置预览区背景颜色（仅界面显示，不影响压制输出）。"""
        self._bg = QColor(hex_str) if hex_str else QColor(self.DEFAULT_BG)
        self.update()

    def bg_color_hex(self) -> str:
        return self._bg.name()

    def paintEvent(self, event) -> None:
        # 与压制管线一致的超采样：在 s 倍画布上渲染样本，再缩回控件尺寸
        s = self._scale
        canvas = QImage(
            max(1, round(self.width() * s)),
            max(1, round(self.height() * s)),
            QImage.Format.Format_ARGB32,
        )
        canvas.fill(self._bg)
        painter = QPainter(canvas)
        painter.scale(s, s)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        style = self._panel.current_style()
        font = QFont()
        families = ([style.font_family] if style.font_family else []) + [
            "Microsoft YaHei",
            "Segoe UI",
            "Segoe UI Emoji",
            "Segoe UI Symbol",
        ]
        font.setFamilies(families)
        font.setPointSize(style.font_size)
        font.setBold(True)
        font.setStyleStrategy(QFont.StyleStrategy.PreferQuality)
        fm = QFontMetrics(font)
        line_height = fm.height()

        # 表情缩放到当前行高，演示表情大小与间距设置的实际效果
        emoji_cache = {
            name: img.scaled(
                line_height, line_height,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            for name, img in self._emoji_images.items()
        }
        builder = DanmakuLayoutBuilder(
            fm=fm,
            emoji_cache=emoji_cache,
            max_content_width=max(60, self.width() - 20),
            line_height=line_height,
            style=style,
        )
        y = 10
        emoji_text = "".join(f"[{name}]" for name in self.EMOJI_NAMES)
        samples = [
            ("弹幕", False),       # 中文 · 普通样式
            ("テスト", False),     # 日文 · 普通样式
            ("sample", True),      # 英文 · 会员样式
            (emoji_text, False),   # 连续表情
        ]
        for text, is_member in samples:
            event = DanmakuEvent(
                time=0.0,
                user="预览",
                text=text,
                is_membership=is_member,
            )
            layout = builder.build(event)
            layout.pre_render(font, emoji_cache)
            painter.drawImage(10, y, layout.cached_image)
            # 行间仅留 2px 间隙（缓存图已含阴影预留），预览更紧凑
            y += layout.height + 2
        painter.end()

        # 超采样画布缩回控件尺寸展示
        out = QPainter(self)
        out.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        out.drawImage(self.rect(), canvas)
        out.end()


def _emoji_page() -> tuple[QWidget, QDoubleSpinBox, QDoubleSpinBox, QSpinBox]:
    """构建表情样式页，返回 (页面, 不透明度, 大小比例, 间距)。"""
    page = QWidget()
    layout = QFormLayout(page)
    layout.setContentsMargins(14, 14, 14, 8)
    layout.setVerticalSpacing(16)
    layout.setHorizontalSpacing(14)

    opacity_spin = _opacity_spin()
    opacity_spin.setToolTip("表情整体不透明度（独立于文本不透明度）")
    layout.addRow("不透明度", opacity_spin)

    size_spin = QDoubleSpinBox()
    size_spin.setRange(0.2, 2.0)
    size_spin.setSingleStep(0.05)
    size_spin.setDecimals(2)
    size_spin.setValue(1.0)
    size_spin.setMinimumHeight(38)
    size_spin.setFixedWidth(70)
    size_spin.setToolTip("表情大小比例（相对行高），1.0 = 与行高一致")
    layout.addRow("大小比例", size_spin)

    spacing_spin = QSpinBox()
    spacing_spin.setRange(0, 50)
    spacing_spin.setValue(4)
    spacing_spin.setMinimumHeight(38)
    spacing_spin.setFixedWidth(70)
    spacing_spin.setToolTip("表情间距（像素）")
    layout.addRow("间距", spacing_spin)

    return page, opacity_spin, size_spin, spacing_spin


class StylePanel(QWidget):
    """样式设置面板：自定义开关 + 字体公共行 + 分组 tab。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        header_row = QHBoxLayout()
        header_row.setSpacing(8)
        self.custom_check = CheckMarkCheckBox("使用自定义样式（覆盖配置文件）")
        self.custom_check.toggled.connect(self._update_enabled)
        header_row.addWidget(self.custom_check)
        header_row.addStretch(1)
        self.reset_btn = QPushButton("重置为默认样式")
        self.reset_btn.setToolTip("把界面样式恢复为内置默认样式")
        self.reset_btn.clicked.connect(self.reset_to_default)
        header_row.addWidget(self.reset_btn)
        layout.addLayout(header_row)

        # 左右分栏：左侧控件区（公共行 + 分组 tab），右侧固定预览板块——
        # 调整任意 tab 中的样式时无需滚动即可看到实时效果
        columns = QHBoxLayout()
        columns.setSpacing(14)

        self.content = QWidget()
        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(10)

        common = QFormLayout()
        common.setVerticalSpacing(14)
        common.setHorizontalSpacing(12)
        self.font_combo = QComboBox()
        self.font_combo.addItem("默认（内置回退字体）", "")
        for family in sorted(QFontDatabase.families()):
            self.font_combo.addItem(family, family)
        self.font_combo.setMinimumHeight(38)
        font_row = QHBoxLayout()
        font_row.setSpacing(8)
        font_row.addWidget(self.font_combo)
        self.font_lock_check = CheckMarkCheckBox("锁定")
        self.font_lock_check.setToolTip("锁定后字体类型不可改动，防止误触")
        self.font_lock_check.toggled.connect(self._update_font_lock)
        font_row.addWidget(self.font_lock_check)
        common.addRow("字体类型", font_row)

        size_row = QHBoxLayout()
        size_row.setSpacing(8)
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 72)
        self.font_size_spin.setValue(25)
        self.font_size_spin.setMinimumHeight(38)
        self.font_size_spin.setFixedWidth(80)
        size_row.addWidget(self.font_size_spin)
        size_row.addStretch(1)
        size_row.addWidget(QLabel("预览背景"))
        self.preview_bg_btn = ColorButton()
        self.preview_bg_btn.setToolTip("预览区背景颜色（仅界面显示，不影响压制输出）")
        self.preview_bg_btn.set_color_hex(StylePreview.DEFAULT_BG)
        self.preview_bg_btn.changed.connect(self._apply_preview_bg)
        size_row.addWidget(self.preview_bg_btn)
        common.addRow("字体大小 (pt)", size_row)
        content_layout.addLayout(common)

        self.tabs = QTabWidget()
        self.text_controls = TextStyleControls()
        self.tabs.addTab(self.text_controls, "普通文本")

        member_page = QWidget()
        member_layout = QVBoxLayout(member_page)
        member_layout.setContentsMargins(0, 0, 0, 0)
        member_layout.setSpacing(8)
        self.copy_member_btn = QPushButton("复制普通样式")
        self.copy_member_btn.setToolTip("把普通文本页的样式复制到会员页")
        self.copy_member_btn.clicked.connect(self.copy_text_to_member)
        # 复制按钮放在会员页「颜色」行的右侧，便于对照调整
        self.member_controls = TextStyleControls(color_row_extra=self.copy_member_btn)
        member_layout.addWidget(self.member_controls)
        self.tabs.addTab(member_page, "会员文本")

        emoji_page, self.emoji_opacity_spin, self.emoji_size_spin, self.emoji_spacing_spin = (
            _emoji_page()
        )
        self.tabs.addTab(emoji_page, "表情")
        content_layout.addWidget(self.tabs)

        columns.addWidget(self.content, 1)

        # 固定预览板块：始终与控件区同排可见
        preview_card = QFrame()
        preview_card.setObjectName("previewCard")
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(12, 12, 12, 12)
        preview_layout.setSpacing(8)
        preview_title = QLabel("样式预览")
        preview_title.setObjectName("sectionTitle")
        preview_layout.addWidget(preview_title)
        self.preview = StylePreview(self)
        preview_layout.addWidget(self.preview, 1)
        scale_row = QHBoxLayout()
        scale_row.setSpacing(8)
        scale_row.addWidget(QLabel("渲染精度"))
        self.render_scale_spin = QDoubleSpinBox()
        self.render_scale_spin.setRange(1.0, 4.0)
        self.render_scale_spin.setSingleStep(0.5)
        self.render_scale_spin.setDecimals(1)
        self.render_scale_spin.setValue(1.0)
        self.render_scale_spin.setSuffix(" 倍")
        self.render_scale_spin.setMinimumHeight(28)
        self.render_scale_spin.setToolTip(
            "弹幕文字渲染超采样倍率：预览与压制输出同步生效，越高文字越细腻"
        )
        self.render_scale_spin.valueChanged.connect(self._apply_render_scale)
        scale_row.addWidget(self.render_scale_spin)
        scale_row.addStretch(1)
        preview_layout.addLayout(scale_row)
        preview_card.setFixedWidth(300)
        columns.addWidget(preview_card)

        layout.addLayout(columns)
        self._update_enabled(False)
        self._wire_preview()

    def _update_enabled(self, checked: bool) -> None:
        self.content.setEnabled(checked)
        self._update_font_lock(self.font_lock_check.isChecked())

    def _update_font_lock(self, locked: bool) -> None:
        """锁定后字体下拉不可改动（若整个内容区已禁用则保持禁用）。"""
        self.font_combo.setEnabled((not locked) and self.content.isEnabled())

    def _apply_preview_bg(self) -> None:
        self.preview.set_bg_color(self.preview_bg_btn.color_hex())

    def _apply_render_scale(self, value: float) -> None:
        self.preview.set_render_scale(value)

    def render_scale_value(self) -> float:
        """当前渲染超采样倍率（压制输出与预览共用）。"""
        return self.render_scale_spin.value()

    def _wire_preview(self) -> None:
        """控件变化时刷新实时预览。"""
        self.font_combo.currentIndexChanged.connect(lambda *_: self.preview.update())
        self.font_size_spin.valueChanged.connect(lambda *_: self.preview.update())
        for controls in (self.text_controls, self.member_controls):
            controls.color_btn.changed.connect(self.preview.update)
            controls.opacity_spin.valueChanged.connect(lambda *_: self.preview.update())
            controls.outline_check.toggled.connect(lambda *_: self.preview.update())
            controls.outline_color_btn.changed.connect(self.preview.update)
            controls.outline_width_spin.valueChanged.connect(lambda *_: self.preview.update())
            controls.shadow_check.toggled.connect(lambda *_: self.preview.update())
            controls.shadow_color_btn.changed.connect(self.preview.update)
            controls.shadow_opacity_spin.valueChanged.connect(lambda *_: self.preview.update())
        for spin in (self.emoji_opacity_spin, self.emoji_size_spin, self.emoji_spacing_spin):
            spin.valueChanged.connect(lambda *_: self.preview.update())

    def load_from(self, config: DanmakuConfig) -> None:
        """按配置填充控件（不改动勾选状态）。"""
        style = config.style
        idx = self.font_combo.findData(style.font_family)
        self.font_combo.setCurrentIndex(max(0, idx))
        self.font_size_spin.setValue(style.font_size)
        self.text_controls.load_from(style.text)
        self.member_controls.load_from(style.member_text)
        self.emoji_opacity_spin.setValue(style.emoji.opacity)
        self.emoji_size_spin.setValue(style.emoji.size_ratio)
        self.emoji_spacing_spin.setValue(style.emoji.spacing)

    def copy_text_to_member(self) -> None:
        """把普通文本页的样式复制到会员页。"""
        self.member_controls.load_from(self.text_controls.text_style())

    def reset_to_default(self) -> None:
        """把界面样式恢复为内置默认样式。"""
        self.load_from(DEFAULT_CONFIG)

    def current_style(self) -> LayoutStyle:
        """由当前控件值构造完整 LayoutStyle（供预览等使用）。"""
        return LayoutStyle(
            font_family=self.font_combo.currentData() or "",
            font_size=self.font_size_spin.value(),
            text=self.text_controls.text_style(),
            member_text=self.member_controls.text_style(),
            emoji=EmojiStyle(
                opacity=self.emoji_opacity_spin.value(),
                size_ratio=self.emoji_size_spin.value(),
                spacing=self.emoji_spacing_spin.value(),
            ),
        )

    def is_custom(self) -> bool:
        return self.custom_check.isChecked()

    def style_overrides(self) -> StyleOverrides | None:
        """未勾选返回 None（跟随配置文件），勾选返回完整覆盖项。"""
        if not self.is_custom():
            return None
        style = self.current_style()
        return StyleOverrides(
            font_family=style.font_family,
            font_size=style.font_size,
            text=style.text,
            member_text=style.member_text,
            emoji=style.emoji,
        )

    def save_state(self) -> None:
        """把面板状态（勾选态 + 控件值）持久化到用户目录。"""
        state = {
            "custom": self.is_custom(),
            "font_family": self.font_combo.currentData() or "",
            "font_size": self.font_size_spin.value(),
            "text": asdict(self.text_controls.text_style()),
            "member_text": asdict(self.member_controls.text_style()),
            "emoji": {
                "opacity": self.emoji_opacity_spin.value(),
                "size_ratio": self.emoji_size_spin.value(),
                "spacing": self.emoji_spacing_spin.value(),
            },
            "preview_bg": self.preview_bg_btn.color_hex() or StylePreview.DEFAULT_BG,
            "font_lock": self.font_lock_check.isChecked(),
            "render_scale": self.render_scale_spin.value(),
        }
        try:
            gui_style_path().write_text(
                json.dumps(state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    def restore_state(self) -> None:
        """从用户目录恢复上次的面板状态；文件缺失或损坏时静默忽略。"""
        try:
            state = json.loads(gui_style_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        try:
            style = LayoutStyle(
                font_family=state.get("font_family", DEFAULT_CONFIG.style.font_family),
                font_size=state.get("font_size", DEFAULT_CONFIG.style.font_size),
                text=TextStyle(**state.get("text", {})),
                member_text=TextStyle(**state.get("member_text", {})),
                emoji=EmojiStyle(**state.get("emoji", {})),
            )
        except (TypeError, ValueError):
            return
        self.load_from(DanmakuConfig(style=style))
        self.custom_check.setChecked(bool(state.get("custom", False)))
        self.preview_bg_btn.set_color_hex(
            state.get("preview_bg") or StylePreview.DEFAULT_BG
        )
        self.font_lock_check.setChecked(bool(state.get("font_lock", False)))
        self.render_scale_spin.setValue(
            float(state.get("render_scale", 1.0) or 1.0)
        )
