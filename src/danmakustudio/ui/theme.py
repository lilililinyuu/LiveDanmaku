"""DanmakuStudio 的 Qt 视觉主题。"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QLinearGradient,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
    QPolygon,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QStyle,
    QStyleOptionButton,
    QWidget,
)

APP_STYLESHEET = """
QWidget {
    color: #172033;
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 10pt;
}

QWidget#appRoot, QDialog {
    background: #f4f6fb;
}

QLabel#brandMark {
    min-width: 48px;
    max-width: 48px;
    min-height: 48px;
    max-height: 48px;
    color: white;
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 1,
        stop: 0 #6868e8, stop: 1 #8b5cf6
    );
    border-radius: 14px;
    font-size: 20pt;
    font-weight: 700;
}

QLabel#pageTitle {
    color: #111827;
    font-size: 19pt;
    font-weight: 700;
}

QLabel#pageSubtitle, QLabel#sectionDescription, QLabel#fieldHint,
QLabel#dialogHint {
    color: #6b7280;
}

QLabel#sectionTitle {
    color: #172033;
    font-size: 12pt;
    font-weight: 650;
}

QLabel#statusBadge {
    border-radius: 11px;
    padding: 5px 11px;
    font-size: 9pt;
    font-weight: 600;
}

QLabel#statusBadge[state="idle"] {
    color: #5f6b7c;
    background: #e8ecf3;
}

QLabel#statusBadge[state="ready"] {
    color: #5046b8;
    background: #e9e7ff;
}

QLabel#statusBadge[state="busy"] {
    color: #9a5a09;
    background: #fff0cf;
}

QLabel#statusBadge[state="done"] {
    color: #08775f;
    background: #dff7ef;
}

QLabel#statusBadge[state="error"] {
    color: #b4233d;
    background: #ffe4e9;
}

QFrame#card {
    background: #ffffff;
    border: 1px solid #e0e5ee;
    border-radius: 14px;
}

QFrame#previewCard {
    background: #f9fafc;
    border: 1px solid #e0e5ee;
    border-radius: 12px;
}

QFrame#sidebar {
    background: #ffffff;
    border: 1px solid #e0e5ee;
    border-radius: 14px;
}

QLabel#sidebarTitle {
    color: #8a93a5;
    font-size: 9pt;
    font-weight: 650;
    padding: 2px 4px 0 4px;
}

QListWidget#navList {
    background: transparent;
    border: none;
    outline: none;
    padding: 0;
    font-size: 10.5pt;
}

QListWidget#navList::item {
    color: #414b5e;
    border-radius: 9px;
    padding: 10px 12px;
    margin: 2px 0;
}

QListWidget#navList::item:hover {
    background: #f0f2f6;
}

QListWidget#navList::item:selected {
    color: #34348f;
    background: #ecebff;
    font-weight: 600;
}

QPushButton#navToggle {
    min-width: 34px;
    max-width: 34px;
    min-height: 34px;
    max-height: 34px;
    color: #414b5e;
    background: #f0f2f6;
    border: 1px solid #e0e5ee;
    border-radius: 9px;
    padding: 0;
    font-size: 13pt;
    font-weight: 700;
}

QPushButton#navToggle:hover {
    border-color: #aaa8ed;
    background: #ecebff;
}

QSplitter::handle {
    background: #e3e8f1;
}

QSplitter::handle:horizontal {
    width: 5px;
    margin: 12px 2px;
    border-radius: 2px;
}

QSplitter::handle:vertical {
    height: 5px;
    margin: 2px 12px;
    border-radius: 2px;
}

QSplitter::handle:hover {
    background: #aaa8ed;
}

QScrollArea#settingsScroll, QWidget#scrollBody {
    background: transparent;
    border: none;
}

QWidget#stylePreview {
    border: 1px solid #252d3e;
    border-radius: 10px;
}

QLabel#fieldLabel {
    color: #414b5e;
    font-weight: 600;
}

QLineEdit, QComboBox {
    min-height: 38px;
    color: #172033;
    background: #f9fafc;
    border: 1px solid #d8dee9;
    border-radius: 9px;
    padding: 0 12px;
    selection-background-color: #6969dc;
}

QLineEdit:hover, QComboBox:hover {
    border-color: #b8c0cf;
    background: #ffffff;
}

QLineEdit:focus, QComboBox:focus {
    border: 2px solid #6969dc;
    padding: 0 11px;
    background: #ffffff;
}

QLineEdit:disabled, QComboBox:disabled {
    color: #9aa3b1;
    background: #f0f2f6;
}

QComboBox::drop-down {
    width: 30px;
    border: none;
}

QComboBox QAbstractItemView {
    color: #172033;
    background: #ffffff;
    border: 1px solid #d8dee9;
    border-radius: 8px;
    padding: 5px;
    selection-color: #34348f;
    selection-background-color: #ecebff;
    outline: none;
}

QSpinBox, QDoubleSpinBox {
    min-height: 38px;
    color: #172033;
    background: #f9fafc;
    border: 1px solid #d8dee9;
    border-radius: 9px;
    padding: 0 10px;
    selection-background-color: #6969dc;
}

QSpinBox:hover, QDoubleSpinBox:hover {
    border-color: #b8c0cf;
    background: #ffffff;
}

QSpinBox:focus, QDoubleSpinBox:focus {
    border: 2px solid #6969dc;
    padding: 0 9px;
    background: #ffffff;
}

QSpinBox:disabled, QDoubleSpinBox:disabled {
    color: #9aa3b1;
    background: #f0f2f6;
}

QTabWidget::pane {
    border: 1px solid #dfe4ec;
    border-radius: 8px;
    background: #ffffff;
    top: -1px;
}

QTabBar::tab {
    background: #f0f2f6;
    color: #414b5e;
    border: 1px solid #dfe4ec;
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 7px 16px;
    margin-right: 2px;
    font-weight: 600;
}

QTabBar::tab:selected {
    background: #ffffff;
    color: #34348f;
}

QTabBar::tab:disabled {
    color: #a1a9b6;
}

QCheckBox {
    spacing: 8px;
    color: #414b5e;
}

QCheckBox::indicator {
    width: 17px;
    height: 17px;
    border: 1px solid #c7cfdb;
    border-radius: 5px;
    background: #ffffff;
}

QCheckBox::indicator:hover {
    border-color: #6969dc;
}

QCheckBox::indicator:checked {
    border-color: #6969dc;
    background: #6969dc;
}

QCheckBox::indicator:checked:disabled {
    border-color: #aeb0d4;
    background: #aeb0d4;
}

QPushButton {
    min-height: 38px;
    color: #30394a;
    background: #ffffff;
    border: 1px solid #d7dde8;
    border-radius: 9px;
    padding: 0 15px;
    font-weight: 600;
}

QPushButton:hover {
    color: #34348f;
    background: #f5f4ff;
    border-color: #aaa8ed;
}

QPushButton:pressed {
    background: #e9e7ff;
}

QPushButton:disabled {
    color: #a1a9b6;
    background: #f0f2f6;
    border-color: #e3e7ed;
}

QPushButton#primaryButton {
    min-height: 42px;
    color: #ffffff;
    background: #6262d9;
    border: 1px solid #6262d9;
    padding: 0 22px;
}

QPushButton#primaryButton:hover {
    background: #5353c8;
    border-color: #5353c8;
}

QPushButton#primaryButton:pressed {
    background: #4646b5;
}

QPushButton#primaryButton:disabled {
    color: #c8c9e8;
    background: #b2b3d9;
    border-color: #b2b3d9;
}

QPushButton#pathButton {
    min-height: 34px;
    color: #5353c8;
    background: transparent;
    border: none;
    padding: 0 8px;
    text-align: left;
    font-weight: 500;
}

QPushButton#pathButton:hover {
    color: #3f3fa7;
    background: #efefff;
}

QTextEdit#logView {
    color: #dce3f0;
    background: #161c2a;
    border: 1px solid #252d3e;
    border-radius: 10px;
    padding: 10px 12px;
    selection-color: #ffffff;
    selection-background-color: #5757c7;
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 9.5pt;
}

QProgressBar {
    min-height: 10px;
    max-height: 10px;
    color: transparent;
    background: #e9edf3;
    border: none;
    border-radius: 5px;
}

QProgressBar::chunk {
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 0,
        stop: 0 #6868e8, stop: 1 #8b5cf6
    );
    border-radius: 5px;
}

QTreeView#fileTree {
    color: #263044;
    background: #ffffff;
    alternate-background-color: #f8f9fc;
    border: 1px solid #dfe4ec;
    border-radius: 10px;
    padding: 4px;
    outline: none;
}

QTreeView#fileTree::item {
    min-height: 30px;
    border-radius: 6px;
    padding: 2px 5px;
}

QTreeView#fileTree::item:hover {
    background: #f0efff;
}

QTreeView#fileTree::item:selected {
    color: #34348f;
    background: #e6e5ff;
}

QHeaderView::section {
    color: #6b7280;
    background: #f7f8fb;
    border: none;
    border-bottom: 1px solid #e3e7ee;
    padding: 7px 8px;
    font-weight: 600;
}

QScrollBar:vertical {
    width: 10px;
    background: transparent;
    margin: 3px;
}

QScrollBar::handle:vertical {
    min-height: 30px;
    background: #c8cfdb;
    border-radius: 4px;
}

QScrollBar::handle:vertical:hover {
    background: #aeb7c6;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    height: 0;
    background: transparent;
}
"""


class CheckMarkCheckBox(QCheckBox):
    """在主题色选中框上补绘清晰白色勾号。"""

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        if not self.isChecked():
            return

        option = QStyleOptionButton()
        self.initStyleOption(option)
        indicator = self.style().subElementRect(
            QStyle.SubElement.SE_CheckBoxIndicator,
            option,
            self,
        )
        if not indicator.isValid():
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(
            QPen(
                QColor("#ffffff"),
                2.0,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.drawPolyline(
            QPolygon(
                [
                    QPoint(indicator.left() + 4, indicator.center().y()),
                    QPoint(indicator.left() + 7, indicator.bottom() - 4),
                    QPoint(indicator.right() - 3, indicator.top() + 4),
                ]
            )
        )
        painter.end()


def create_app_icon() -> QIcon:
    """生成无需外部资源的简洁应用图标。"""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gradient = QLinearGradient(0, 0, size, size)
        gradient.setColorAt(0.0, QColor("#6868e8"))
        gradient.setColorAt(1.0, QColor("#8b5cf6"))
        margin = max(1.0, size * 0.06)
        icon_rect = QRectF(margin, margin, size - margin * 2, size - margin * 2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawRoundedRect(icon_rect, size * 0.22, size * 0.22)

        painter.setBrush(QColor("#ffffff"))
        painter.setPen(
            QPen(
                QColor("#ffffff"),
                max(1.5, size * 0.055),
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
            )
        )
        for row, line_end in ((0.32, 0.73), (0.50, 0.67), (0.68, 0.76)):
            y = round(size * row)
            radius = max(1, round(size * 0.035))
            painter.drawEllipse(QPoint(round(size * 0.28), y), radius, radius)
            painter.drawLine(
                QPoint(round(size * 0.40), y),
                QPoint(round(size * line_end), y),
            )
        painter.end()
        icon.addPixmap(pixmap)

    return icon


def apply_theme(app: QApplication) -> None:
    """在应用级别启用一致的 Fusion 浅色主题。"""
    app.setApplicationName("DanmakuStudio")
    app.setApplicationDisplayName("DanmakuStudio")
    app.setWindowIcon(create_app_icon())
    app.setStyle("Fusion")
    font = QFont("Microsoft YaHei UI", 10)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    app.setFont(font)
    app.setStyleSheet(APP_STYLESHEET)


def refresh_dynamic_style(widget: QWidget) -> None:
    """动态属性变化后刷新单个组件的 QSS 匹配。"""
    style = widget.style()
    if style is None:
        return
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
