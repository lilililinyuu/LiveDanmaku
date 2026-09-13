"""DanmakuStudio 主窗口。"""

from __future__ import annotations

import os
import re
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..batch import BatchJob, collect_batch_jobs, find_matching_subtitle
from ..config.loader import config_to_yaml, load_config
from ..core.recorder import RecordParams
from ..config.models import (
    DEFAULT_CONFIG,
    DanmakuConfig,
    EncodeMode,
    RecorderParams,
    apply_style_overrides,
)
from ..errors import DanmakuStudioError
from ..input.parser import parse_subtitle
from ..input.trim import (
    default_trimmed_path,
    trim_chat_json_file,
    trim_subtitle_xml_file,
)
from ..utils.validation import SUPPORTED_VIDEO_EXTS
from ._shared import (
    config_dialog_start_location,
    format_duration,
    get_app_dir,
    last_config_dir_path,
    last_dir_path,
)
from .file_picker import FileFolderPickerDialog
from .style_panel import StylePanel
from .theme import CheckMarkCheckBox, refresh_dynamic_style
from .worker import BatchWorker, DownloadWorker, MonitorWorker, RecordWorker

# 参照选择对话框展示的最大条数（与 pipeline 工作台保持一致）
_TRIM_REF_WINDOW = 50


def parse_danmaku_preview_events(path: Path) -> list:
    """按扩展名解析弹幕文件的全部事件（JSON 走聊天解析）。"""
    if path.suffix.lower() == ".json":
        from ..input.chat2xml import preview_json_events

        return preview_json_events(str(path), limit=None)
    return parse_subtitle(str(path))


class TrimReferenceDialog(QDialog):
    """参照选择：保留一条弹幕，其之前的弹幕全部删除。

    列表展示条数可通过「显示条数」自行调整（默认 50 条）。
    """

    def __init__(self, events, window_size: int = _TRIM_REF_WINDOW, parent: QWidget | None = None):
        super().__init__(parent)
        self._events = list(events)
        self.setWindowTitle("删除开头弹幕 · 参照选择")
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        hint = QLabel("选择要保留的一条（点选所在行），它之前的全部弹幕将被删除。")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        count_row = QHBoxLayout()
        count_row.setSpacing(8)
        count_row.addWidget(QLabel("显示条数"))
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, max(1, len(self._events)))
        self.count_spin.setValue(min(window_size, len(self._events)))
        self.count_spin.setToolTip("参照列表展示的弹幕条数")
        self.count_spin.valueChanged.connect(self._rebuild_table)
        count_row.addWidget(self.count_spin)
        count_row.addStretch(1)
        layout.addLayout(count_row)

        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(["序号", "时间", "用户", "内容"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._fill_table(self.count_spin.value())
        self.table.selectRow(0)
        layout.addWidget(self.table)

        self.status_label = QLabel("")
        self._update_status()
        self.table.selectionModel().selectionChanged.connect(self._update_status)
        layout.addWidget(self.status_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保留所选并删除之前弹幕")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _fill_table(self, count: int) -> None:
        """按显示条数填充参照表格。"""
        rows = self._events[:count]
        self.table.setRowCount(len(rows))
        for r, ev in enumerate(rows):
            text = ev.text.replace("\n", " ").strip()
            if len(text) > 60:
                text = text[:60] + "…"
            for col, value in enumerate((str(r + 1), f"{ev.time:.1f}s", ev.user, text)):
                self.table.setItem(r, col, QTableWidgetItem(value))

    def _rebuild_table(self, count: int) -> None:
        """显示条数变化时重建表格，尽量保持原选中行。"""
        selected = self.table.currentRow()
        self._fill_table(count)
        if 0 <= selected < self.table.rowCount():
            self.table.selectRow(selected)
        else:
            self.table.selectRow(0)

    def _update_status(self, *_args) -> None:
        row = max(0, self.table.currentRow())
        self.status_label.setText(f"将删除开头 {row} 条，保留第 {row + 1} 条作为新首条")

    @property
    def keep_index(self) -> int:
        """用户选择保留的弹幕序号（1-based）。"""
        return max(0, self.table.currentRow()) + 1


class TrimExportDialog(QDialog):
    """删除开头 N 条弹幕并导出文件：可选覆盖原文件或生成新文件。"""

    def __init__(self, src: Path, parent: QWidget | None = None):
        super().__init__(parent)
        self.src = src
        self.setWindowTitle("删除开头弹幕 · 导出文件")
        self.setMinimumWidth(600)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        src_label = QLabel(f"源文件：{src}")
        src_label.setWordWrap(True)
        layout.addWidget(src_label)

        form = QFormLayout()
        form.setSpacing(10)
        count_row = QHBoxLayout()
        count_row.setSpacing(8)
        self.count_spin = QSpinBox()
        self.count_spin.setRange(0, 100000)
        self.count_spin.setValue(0)
        self.count_spin.setSuffix(" 条")
        self.count_spin.setToolTip("删除文件开头 N 条弹幕；0 = 不删除，仅按所选方式导出")
        count_row.addWidget(self.count_spin)
        self.btn_reference = QPushButton("参照选择…")
        self.btn_reference.setToolTip("列出该文件开头的弹幕（默认 50 条，可在弹窗中调整），选择保留哪一条")
        self.btn_reference.clicked.connect(self._choose_reference)
        count_row.addWidget(self.btn_reference)
        count_row.addStretch(1)
        form.addRow("删除开头", count_row)

        self.radio_new = QRadioButton("生成新文件")
        self.radio_new.setChecked(True)
        self.radio_overwrite = QRadioButton("覆盖原文件")
        self.radio_overwrite.setToolTip("直接改写源文件（不可恢复，请谨慎选择）")
        form.addRow("导出方式", self.radio_new)
        form.addRow("", self.radio_overwrite)
        layout.addLayout(form)

        self.target_label = QLabel("")
        self.target_label.setObjectName("fieldHint")
        self.target_label.setWordWrap(True)
        layout.addWidget(self.target_label)
        self.count_spin.valueChanged.connect(self._update_target)
        self.radio_new.toggled.connect(self._update_target)
        self._update_target()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("导出")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _choose_reference(self) -> None:
        events = parse_danmaku_preview_events(self.src)
        if not events:
            QMessageBox.warning(self, "提示", "该文件没有可展示的弹幕事件。")
            return
        dialog = TrimReferenceDialog(events, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.count_spin.setValue(max(0, dialog.keep_index - 1))

    def _update_target(self, *_args) -> None:
        if self.radio_new.isChecked():
            target = default_trimmed_path(self.src, self.count_spin.value())
            self.target_label.setText(f"输出文件：{target}")
        else:
            self.target_label.setText("输出文件：覆盖原文件")

    def values(self) -> tuple[int, bool]:
        """返回 (删除条数, 是否覆盖原文件)。"""
        return self.count_spin.value(), self.radio_overwrite.isChecked()


class DanmakuStudioWindow(QWidget):
    """DanmakuStudio 主窗口。"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("DanmakuStudio · 弹幕压制工作台")
        self.setObjectName("appRoot")
        self.resize(1120, 780)
        self.setMinimumSize(900, 640)
        self.worker: BatchWorker | None = None
        self.download_worker: DownloadWorker | None = None
        self.record_worker = None
        self.monitor_worker = None
        self._record_started_at = 0.0
        self.pending_jobs: list[BatchJob] = []
        self.sidebar_collapsed = False
        self.last_dir = str(get_app_dir())
        self.last_config_dir = ""
        self._load_last_dir()
        self._load_last_config_dir()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 14, 20, 14)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(13)
        brand_mark = QLabel("弹")
        brand_mark.setObjectName("brandMark")
        brand_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(brand_mark)

        title_layout = QVBoxLayout()
        title_layout.setSpacing(1)
        title = QLabel("DanmakuStudio")
        title.setObjectName("pageTitle")
        subtitle = QLabel("批量弹幕压制工作台")
        subtitle.setObjectName("pageSubtitle")
        title_layout.addWidget(title)
        title_layout.addWidget(subtitle)
        header.addLayout(title_layout)
        header.addStretch(1)

        self.status_badge = QLabel("等待任务")
        self.status_badge.setObjectName("statusBadge")
        self.status_badge.setProperty("state", "idle")
        self.status_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self.status_badge)
        layout.addLayout(header)

        settings_card = QFrame()
        settings_card.setObjectName("card")
        settings_layout = QVBoxLayout(settings_card)
        settings_layout.setContentsMargins(18, 16, 18, 18)
        settings_layout.setSpacing(13)

        settings_title = QLabel("输出设置")
        settings_title.setObjectName("sectionTitle")
        settings_description = QLabel("选择编码方式，也可以按需加载自定义 YAML 配置。")
        settings_description.setObjectName("sectionDescription")
        settings_layout.addWidget(settings_title)
        settings_layout.addWidget(settings_description)

        options = QGridLayout()
        options.setHorizontalSpacing(12)
        options.setVerticalSpacing(11)
        options.setColumnStretch(1, 1)

        encode_label = QLabel("编码模式")
        encode_label.setObjectName("fieldLabel")
        options.addWidget(encode_label, 0, 0)
        self.encode_combo = QComboBox()
        self.encode_combo.addItem("自动选择（推荐）", EncodeMode.AUTO)
        self.encode_combo.addItem("NVIDIA GPU", EncodeMode.GPU)
        self.encode_combo.addItem("Intel QSV", EncodeMode.QSV)
        self.encode_combo.addItem("CPU", EncodeMode.CPU)
        self.encode_combo.setToolTip("自动模式会优先尝试硬件编码，不可用时回退到 CPU。")
        options.addWidget(self.encode_combo, 0, 1)

        self.force_checkbox = CheckMarkCheckBox("覆盖已存在输出")
        options.addWidget(self.force_checkbox, 0, 2)

        config_label = QLabel("配置文件")
        config_label.setObjectName("fieldLabel")
        options.addWidget(config_label, 1, 0)
        self.config_edit = QLineEdit()
        self.config_edit.setPlaceholderText("留空则使用内置默认配置")
        self.config_edit.setClearButtonEnabled(True)
        default_config = get_app_dir() / "danmakustudio.yaml"
        if default_config.exists():
            self.config_edit.setText(str(default_config))
        self.config_edit.editingFinished.connect(self._reload_style_panel)
        options.addWidget(self.config_edit, 1, 1)

        self.btn_config = QPushButton("浏览…")
        self.btn_config.clicked.connect(self.choose_config)
        options.addWidget(self.btn_config, 1, 2)

        scale_label = QLabel("渲染精度")
        scale_label.setObjectName("fieldLabel")
        scale_row = QHBoxLayout()
        scale_row.setSpacing(8)
        self.render_scale_spin = QDoubleSpinBox()
        self.render_scale_spin.setRange(1.0, 4.0)
        self.render_scale_spin.setSingleStep(0.5)
        self.render_scale_spin.setDecimals(1)
        self.render_scale_spin.setValue(1.0)
        self.render_scale_spin.setSuffix(" 倍")
        self.render_scale_spin.setToolTip(
            "弹幕文字渲染超采样倍率：先按倍率放大渲染再缩回视频尺寸，"
            "越高文字边缘越细腻（内存/CPU 占用略增）；1 = 原始清晰度。"
        )
        scale_row.addWidget(self.render_scale_spin)
        scale_row.addStretch(1)
        first_label = QLabel("首条弹幕时间")
        first_label.setObjectName("fieldLabel")
        options.addWidget(first_label, 2, 0)
        first_row = QHBoxLayout()
        first_row.setSpacing(8)
        self.first_danmaku_check = CheckMarkCheckBox("调整")
        self.first_danmaku_check.setToolTip("勾选后把第一条弹幕对齐到指定视频时间")
        self.first_danmaku_spin = QDoubleSpinBox()
        self.first_danmaku_spin.setRange(0.0, 3600.0)
        self.first_danmaku_spin.setSingleStep(0.5)
        self.first_danmaku_spin.setDecimals(1)
        self.first_danmaku_spin.setValue(2.0)
        self.first_danmaku_spin.setSuffix(" 秒")
        self.first_danmaku_spin.setToolTip(
            "第一条弹幕出现的视频时间（秒）。0=视频一开始就出现；"
            "调大=推迟出现，调小=提前出现。不勾选则保持字幕文件原时间轴。"
        )
        first_row.addWidget(self.first_danmaku_check)
        first_row.addWidget(self.first_danmaku_spin)
        first_row.addStretch(1)
        options.addLayout(first_row, 2, 1)

        ffmpeg_label = QLabel("自定义参数")
        ffmpeg_label.setObjectName("fieldLabel")
        options.addWidget(ffmpeg_label, 3, 0)
        self.ffmpeg_args_edit = QLineEdit()
        self.ffmpeg_args_edit.setPlaceholderText('例如 -maxrate 4M -bufsize 8M（追加在 ffmpeg 命令末尾）')
        self.ffmpeg_args_edit.setClearButtonEnabled(True)
        self.ffmpeg_args_edit.setToolTip(
            "自定义 ffmpeg 压缩参数，追加在命令末尾可覆盖默认压缩设置。"
        )
        options.addWidget(self.ffmpeg_args_edit, 3, 1)

        clip_label = QLabel("测试片段")
        clip_label.setObjectName("fieldLabel")
        options.addWidget(clip_label, 4, 0)
        clip_row = QHBoxLayout()
        clip_row.setSpacing(8)
        self.test_clip_check = CheckMarkCheckBox("只导出测试片段")
        self.test_clip_check.setToolTip("勾选后每个任务只压制一个片段，用于先确认效果")
        self.clip_duration_spin = QDoubleSpinBox()
        self.clip_duration_spin.setRange(1.0, 3600.0)
        self.clip_duration_spin.setSingleStep(10.0)
        self.clip_duration_spin.setDecimals(0)
        self.clip_duration_spin.setValue(60.0)
        self.clip_duration_spin.setSuffix(" 秒")
        self.clip_duration_spin.setToolTip("测试片段时长")
        self.clip_start_check = CheckMarkCheckBox("自定义起点")
        self.clip_start_check.setToolTip(
            "勾选后从指定的视频时间开始压制测试片段；不勾选则从首条弹幕处开始"
        )
        self.clip_start_spin = QDoubleSpinBox()
        self.clip_start_spin.setRange(0.0, 36000.0)
        self.clip_start_spin.setSingleStep(1.0)
        self.clip_start_spin.setDecimals(1)
        self.clip_start_spin.setValue(0.0)
        self.clip_start_spin.setSuffix(" 秒")
        self.clip_start_spin.setToolTip("测试片段起始时间（视频秒数）")
        self.clip_start_check.setEnabled(False)
        self.clip_start_spin.setEnabled(False)
        self.test_clip_check.toggled.connect(self._update_clip_start_enabled)
        self.clip_start_check.toggled.connect(self._update_clip_start_enabled)
        clip_row.addWidget(self.test_clip_check)
        clip_row.addWidget(self.clip_duration_spin)
        clip_row.addWidget(self.clip_start_check)
        clip_row.addWidget(self.clip_start_spin)
        clip_row.addStretch(1)
        options.addLayout(clip_row, 4, 1)

        drop_label = QLabel("删除开头弹幕")
        drop_label.setObjectName("fieldLabel")
        options.addWidget(drop_label, 5, 0)
        drop_row = QHBoxLayout()
        drop_row.setSpacing(8)
        self.drop_first_spin = QSpinBox()
        self.drop_first_spin.setRange(0, 100000)
        self.drop_first_spin.setValue(0)
        self.drop_first_spin.setSuffix(" 条")
        self.drop_first_spin.setToolTip(
            "删除开头 N 条弹幕（如直播开始前发出的测试弹幕），0=不删除。"
            "可点击「参照选择…」从开头的弹幕中指定保留哪一条（默认展示 50 条，可调整）。"
        )
        self.btn_drop_reference = QPushButton("参照选择…")
        self.btn_drop_reference.setToolTip(
            "列出弹幕文件开头若干条（默认 50，可在弹窗中调整；序号/时间/用户/内容），选择保留哪一条，其之前的弹幕全部删除"
        )
        self.btn_drop_reference.clicked.connect(self.choose_drop_first_reference)
        drop_row.addWidget(self.drop_first_spin)
        drop_row.addWidget(self.btn_drop_reference)
        drop_row.addStretch(1)
        options.addLayout(drop_row, 5, 1)

        settings_layout.addLayout(options)

        style_card = QFrame()
        style_card.setObjectName("card")
        style_layout = QVBoxLayout(style_card)
        style_layout.setContentsMargins(18, 16, 18, 18)
        style_layout.setSpacing(12)
        style_header = QHBoxLayout()
        style_title = QLabel("样式设置")
        style_title.setObjectName("sectionTitle")
        style_header.addWidget(style_title)
        style_header.addStretch(1)
        self.btn_save_style = QPushButton("保存样式到 YAML…")
        self.btn_save_style.setToolTip("把当前界面样式合并进配置并保存为 YAML 文件")
        self.btn_save_style.clicked.connect(self.save_style_to_yaml)
        style_header.addWidget(self.btn_save_style)
        style_layout.addLayout(style_header)
        style_description = QLabel("可直接调整弹幕样式；勾选后覆盖配置文件，不勾选则完全跟随配置文件。")
        style_description.setObjectName("sectionDescription")
        style_layout.addWidget(style_description)
        self.style_panel = StylePanel()
        # 渲染精度控件位于样式面板，别名供运行锁与压制参数使用
        self.render_scale_spin = self.style_panel.render_scale_spin
        style_layout.addWidget(self.style_panel)

        # 侧边导航 + 功能页：压制设置 / 弹幕样式 / JSON 转 XML / 下载内容
        self.pages = QStackedWidget()
        self.pages.addWidget(self._wrap_scroll(settings_card))
        self.pages.addWidget(self._wrap_scroll(style_card))
        self.pages.addWidget(self._build_json_page())
        self.pages.addWidget(self._build_download_page())
        self.pages.addWidget(self._build_record_page())

        sidebar = self._build_sidebar()
        self.nav_list.currentRowChanged.connect(self.pages.setCurrentIndex)

        top_split = QSplitter(Qt.Orientation.Horizontal)
        top_split.setObjectName("topSplit")
        top_split.setChildrenCollapsible(False)
        top_split.addWidget(sidebar)
        top_split.addWidget(self.pages)
        top_split.setStretchFactor(0, 0)
        top_split.setStretchFactor(1, 1)
        top_split.setSizes([176, 940])

        task_card = QFrame()
        task_card.setObjectName("card")
        task_layout = QVBoxLayout(task_card)
        task_layout.setContentsMargins(18, 16, 18, 18)
        task_layout.setSpacing(12)

        task_header = QHBoxLayout()
        task_heading_layout = QVBoxLayout()
        task_heading_layout.setSpacing(2)
        task_title = QLabel("任务队列")
        task_title.setObjectName("sectionTitle")
        task_description = QLabel("可同时选择多个视频文件或文件夹，字幕会自动匹配。")
        task_description.setObjectName("sectionDescription")
        task_heading_layout.addWidget(task_title)
        task_heading_layout.addWidget(task_description)
        task_header.addLayout(task_heading_layout)
        task_header.addStretch(1)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.btn_choose = QPushButton("选择视频或文件夹")
        self.btn_choose.clicked.connect(self.choose_items)
        actions.addWidget(self.btn_choose)

        self.btn_preview = QPushButton("生成预览图…")
        self.btn_preview.setToolTip("选一个视频，用当前设置渲染弹幕预览 PNG（压制前确认效果）")
        self.btn_preview.clicked.connect(self.generate_preview_images)
        actions.addWidget(self.btn_preview)

        self.btn_start = QPushButton("开始压制  →")
        self.btn_start.setObjectName("primaryButton")
        self.btn_start.setEnabled(False)
        self.btn_start.clicked.connect(self.start_pending_jobs)
        actions.addWidget(self.btn_start)
        task_header.addLayout(actions)
        task_layout.addLayout(task_header)

        self.log = QTextEdit()
        self.log.setObjectName("logView")
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("选择素材后，这里会显示匹配结果和处理日志。")
        self.log.document().setMaximumBlockCount(5000)
        task_layout.addWidget(self.log, 1)

        self.summary_label = QLabel("等待选择任务")
        self.summary_label.setObjectName("fieldHint")
        task_layout.addWidget(self.summary_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setAccessibleName("批量处理进度")
        task_layout.addWidget(self.progress_bar)

        # 上下分割：功能页固定在上，任务队列/日志固定在下，可拖动调节高度
        task_card.setMinimumHeight(220)
        main_split = QSplitter(Qt.Orientation.Vertical)
        main_split.setObjectName("mainSplit")
        main_split.setChildrenCollapsible(False)
        main_split.addWidget(top_split)
        main_split.addWidget(task_card)
        main_split.setStretchFactor(0, 1)
        main_split.setStretchFactor(1, 0)
        main_split.setSizes([500, 290])
        layout.addWidget(main_split, 1)

        self._record_timer = QTimer(self)
        self._record_timer.setInterval(1000)
        self._record_timer.timeout.connect(self._update_record_status)
        self._record_timer.start()

        self._reload_style_panel()
        self.style_panel.restore_state()

    def closeEvent(self, event) -> None:
        self.style_panel.save_state()
        super().closeEvent(event)

    def _busy(self) -> bool:
        """是否有批量压制或下载任务正在后台运行。"""
        return (self.worker is not None and self.worker.isRunning()) or (
            self.download_worker is not None and self.download_worker.isRunning()
        )

    def _set_status(self, text: str, state: str) -> None:
        self.status_badge.setText(text)
        self.status_badge.setProperty("state", state)
        refresh_dynamic_style(self.status_badge)

    def _load_last_dir(self) -> None:
        cfg = last_dir_path()
        if not cfg.exists():
            return

        try:
            path = cfg.read_text(encoding="utf-8").strip()
        except OSError:
            return

        if path and os.path.isdir(path):
            self.last_dir = path

    def _save_last_dir(self, dir_path: str) -> None:
        try:
            last_dir_path().write_text(dir_path, encoding="utf-8")
        except OSError:
            pass

    def _load_last_config_dir(self) -> None:
        cfg = last_config_dir_path()
        if not cfg.exists():
            return

        try:
            path = cfg.read_text(encoding="utf-8").strip()
        except OSError:
            return

        if path and os.path.isdir(path):
            self.last_config_dir = path

    def _save_last_config_dir(self, dir_path: str) -> None:
        try:
            last_config_dir_path().write_text(dir_path, encoding="utf-8")
        except OSError:
            pass

    def _reload_style_panel(self) -> None:
        """按当前配置文件路径刷新样式面板的控件值。"""
        path = self.config_edit.text().strip() or None
        try:
            config = load_config(path)
        except DanmakuStudioError:
            return
        self.style_panel.load_from(config)

    def _wrap_scroll(self, widget: QWidget) -> QScrollArea:
        """把页面内容包进滚动区：高度不足时滚动；卡片贴内容高度不拉伸出空白。"""
        scroll = QScrollArea()
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self._hug_content_page(widget))
        return scroll

    def _build_sidebar(self) -> QFrame:
        """侧边导航栏：功能页切换与收起/展开。"""
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(10, 12, 10, 12)
        side_layout.setSpacing(10)

        self.btn_toggle_sidebar = QPushButton("«")
        self.btn_toggle_sidebar.setObjectName("navToggle")
        self.btn_toggle_sidebar.setToolTip("收起侧边栏")
        self.btn_toggle_sidebar.clicked.connect(self._toggle_sidebar)
        side_layout.addWidget(self.btn_toggle_sidebar, 0, Qt.AlignmentFlag.AlignLeft)

        self.sidebar_title = QLabel("功能导航")
        self.sidebar_title.setObjectName("sidebarTitle")
        side_layout.addWidget(self.sidebar_title)

        self.nav_list = QListWidget()
        self.nav_list.setObjectName("navList")
        for label in ("压制设置", "弹幕样式", "JSON 转 XML", "下载内容", "直播录制"):
            self.nav_list.addItem(label)
        self.nav_list.setCurrentRow(0)
        # 纵向填满侧边栏：功能项少时不留底部空白，也不会压缩出内部滚动
        self.nav_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        side_layout.addWidget(self.nav_list)

        self.sidebar = sidebar
        return sidebar

    def _toggle_sidebar(self) -> None:
        """收起/展开侧边栏；收起后仅保留窄条与展开按钮。"""
        self.sidebar_collapsed = not self.sidebar_collapsed
        if self.sidebar_collapsed:
            self.sidebar_title.setVisible(False)
            self.nav_list.setVisible(False)
            self.sidebar.setFixedWidth(48)
            self.btn_toggle_sidebar.setText("»")
            self.btn_toggle_sidebar.setToolTip("展开侧边栏")
        else:
            self.sidebar.setMinimumWidth(150)
            self.sidebar.setMaximumWidth(16777215)
            self.sidebar_title.setVisible(True)
            self.nav_list.setVisible(True)
            self.btn_toggle_sidebar.setText("«")
            self.btn_toggle_sidebar.setToolTip("收起侧边栏")

    def _build_json_page(self) -> QWidget:
        """「JSON 转 XML」功能页。"""
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(12)

        title = QLabel("JSON 转 XML")
        title.setObjectName("sectionTitle")
        description = QLabel(
            "把 YouTube 直播回放聊天 JSON（yt-dlp 导出）转换为弹幕 XML；"
            "检测到同名视频时会询问是否直接加入任务队列。"
        )
        description.setObjectName("sectionDescription")
        description.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(description)

        form = QFormLayout()
        form.setSpacing(10)
        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        self.json_path_edit = QLineEdit()
        self.json_path_edit.setPlaceholderText("选择聊天 JSON 或弹幕 XML 文件")
        self.btn_json_browse = QPushButton("浏览…")
        self.btn_json_browse.clicked.connect(self.browse_chat_json)
        path_row.addWidget(self.json_path_edit, 1)
        path_row.addWidget(self.btn_json_browse)
        form.addRow("聊天 JSON", path_row)
        layout.addLayout(form)

        action_row = QHBoxLayout()
        action_row.setSpacing(10)
        self.btn_trim = QPushButton("删除开头弹幕…")
        self.btn_trim.setToolTip(
            "删除文件开头 N 条弹幕并导出（JSON/XML；可选覆盖原文件或生成新文件）"
        )
        self.btn_trim.clicked.connect(self.trim_danmaku_file)
        action_row.addWidget(self.btn_trim)
        self.btn_json_convert = QPushButton("转换为 XML")
        self.btn_json_convert.clicked.connect(self.start_json_convert)
        action_row.addWidget(self.btn_json_convert)
        self.btn_highlights = QPushButton("分析高潮点")
        self.btn_highlights.setToolTip(
            "按弹幕发送频率分析聊天 JSON：高潮点 = 相对前后区段明显更密集，"
            "单段最长 5 分钟"
        )
        self.btn_highlights.clicked.connect(self.analyze_chat_highlights)
        action_row.addWidget(self.btn_highlights)
        self.highlight_top_spin = QSpinBox()
        self.highlight_top_spin.setRange(1, 20)
        self.highlight_top_spin.setValue(5)
        self.highlight_top_spin.setPrefix("前 ")
        self.highlight_top_spin.setSuffix(" 名")
        self.highlight_top_spin.setToolTip("统计弹幕最密集的前多少个时间段")
        action_row.addWidget(self.highlight_top_spin)
        action_row.addStretch(1)
        layout.addLayout(action_row)
        return self._hug_content_page(card)

    def _build_record_page(self) -> QWidget:
        """「直播录制」功能页：录制进行中的直播 / 监控频道开播自动录制。"""
        try:
            recorder = load_config(self.config_edit.text().strip() or None).recorder
        except (DanmakuStudioError, AttributeError):
            recorder = DEFAULT_CONFIG.recorder

        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(12)

        title = QLabel("直播录制")
        title.setObjectName("sectionTitle")
        description = QLabel(
            "用 yt-dlp 录制直播：可录制进行中的直播（支持从开头录制），"
            "也可监控频道、开播时自动开始录制；录制中可随时保存已录制的片段。"
        )
        description.setObjectName("sectionDescription")
        description.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(description)

        form = QFormLayout()
        form.setSpacing(10)
        self.record_url_edit = QLineEdit()
        self.record_url_edit.setPlaceholderText(
            "直播 URL / 视频 URL；监控开播请填频道链接（如 https://www.youtube.com/@handle）"
        )
        form.addRow("地址", self.record_url_edit)

        dir_row = QHBoxLayout()
        dir_row.setSpacing(8)
        self.record_dir_edit = QLineEdit()
        self.record_dir_edit.setPlaceholderText("录制输出目录")
        self.record_dir_edit.setText(self.last_dir)
        self.btn_record_browse = QPushButton("浏览…")
        self.btn_record_browse.clicked.connect(self.browse_record_dir)
        dir_row.addWidget(self.record_dir_edit, 1)
        dir_row.addWidget(self.btn_record_browse)
        form.addRow("输出目录", dir_row)

        self.record_fmt_edit = QLineEdit(recorder.fmt)
        self.record_fmt_edit.setToolTip(
            "单文件格式（如 best）录制中即为完整可播放文件，"
            "随时保存片段才能得到带音轨的内容；也可改写为其它 yt-dlp 格式"
        )
        form.addRow("格式 (-f)", self.record_fmt_edit)

        opts_row = QHBoxLayout()
        opts_row.setSpacing(12)
        self.record_live_from_start_check = CheckMarkCheckBox("从直播开头录制")
        self.record_live_from_start_check.setChecked(recorder.live_from_start)
        self.record_live_from_start_check.setToolTip(
            "--live-from-start：从直播开头录制；对不支持回放的直播自动忽略"
        )
        opts_row.addWidget(self.record_live_from_start_check)
        self.record_chat_check = CheckMarkCheckBox("同步录制弹幕 JSON")
        self.record_chat_check.setChecked(recorder.record_chat)
        self.record_chat_check.setToolTip(
            "录制时附带抓取 live_chat 弹幕，结束后可直接转 XML 压制"
        )
        opts_row.addWidget(self.record_chat_check)
        opts_row.addStretch(1)
        form.addRow("选项", opts_row)

        self.record_proxy_edit = QLineEdit(recorder.proxy)
        self.record_proxy_edit.setPlaceholderText("留空 = 不使用代理")
        self.record_proxy_edit.setToolTip("yt-dlp --proxy，例如 socks5://127.0.0.1:7897")
        form.addRow("代理", self.record_proxy_edit)

        self.record_pot_edit = QLineEdit(recorder.pot_token_script)
        self.record_pot_edit.setPlaceholderText("bgutil-pot 路径（留空 = 不传）")
        self.record_pot_edit.setToolTip(
            '以 --extractor-args "youtubepot-bgutilscript:script_path=…" 传入'
        )
        form.addRow("PO Token", self.record_pot_edit)
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.btn_record_start = QPushButton("开始录制")
        self.btn_record_start.setObjectName("primaryButton")
        self.btn_record_start.clicked.connect(self.start_recording)
        btn_row.addWidget(self.btn_record_start)
        self.btn_record_stop = QPushButton("停止录制")
        self.btn_record_stop.setToolTip("结束录制并保留已录制的文件")
        self.btn_record_stop.setEnabled(False)
        self.btn_record_stop.clicked.connect(self.stop_recording)
        btn_row.addWidget(self.btn_record_stop)
        self.btn_record_save = QPushButton("保存当前片段")
        self.btn_record_save.setToolTip(
            "把已录制的部分复制一份保存（默认保存到 输出目录/已保存/），不中断录制"
        )
        self.btn_record_save.setEnabled(False)
        self.btn_record_save.clicked.connect(self.save_record_snapshot)
        btn_row.addWidget(self.btn_record_save)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        monitor_row = QHBoxLayout()
        monitor_row.setSpacing(10)
        self.btn_monitor_start = QPushButton("监控此频道开播（自动录制）")
        self.btn_monitor_start.setToolTip(
            "周期探测该频道是否开播，开播时自动开始录制（间隔见配置 recorder.monitor_interval）"
        )
        self.btn_monitor_start.clicked.connect(self.start_monitor)
        monitor_row.addWidget(self.btn_monitor_start)
        self.btn_monitor_stop = QPushButton("停止监控")
        self.btn_monitor_stop.setEnabled(False)
        self.btn_monitor_stop.clicked.connect(self.stop_monitor)
        monitor_row.addWidget(self.btn_monitor_stop)
        monitor_row.addStretch(1)
        layout.addLayout(monitor_row)

        self.record_status_label = QLabel("未在录制")
        self.record_status_label.setObjectName("fieldHint")
        layout.addWidget(self.record_status_label)
        layout.addStretch(1)
        return self._hug_content_page(card)

    def browse_record_dir(self) -> None:
        dir_path = QFileDialog.getExistingDirectory(
            self, "选择录制输出目录",
            self.record_dir_edit.text().strip() or self.last_dir,
        )
        if dir_path:
            self.record_dir_edit.setText(dir_path)
            self._remember_selection_dir([dir_path], self.last_dir)

    def _record_params(self, url: str) -> RecordParams:
        """按界面当前设置构造录制参数（core.recorder.RecordParams）。"""
        return RecordParams(
            url=url,
            output_dir=Path(self.record_dir_edit.text().strip() or self.last_dir),
            fmt=self.record_fmt_edit.text().strip() or "best",
            live_from_start=self.record_live_from_start_check.isChecked(),
            proxy=self.record_proxy_edit.text().strip(),
            pot_token_script=self.record_pot_edit.text().strip(),
            record_chat=self.record_chat_check.isChecked(),
        )

    def _begin_record(self, params: RecordParams) -> None:
        self.record_url_edit.setText(params.url)
        self.btn_record_start.setEnabled(False)
        self.btn_record_stop.setEnabled(True)
        self.btn_record_save.setEnabled(True)
        self.btn_monitor_start.setEnabled(False)
        self._set_status("录制中", "busy")
        self._record_started_at = time.perf_counter()
        self.append_log("")
        self.append_log(f"开始录制: {params.url}")
        self.append_log(f"  输出目录: {params.output_dir}")
        self.record_worker = RecordWorker(params)
        self.record_worker.log.connect(self.append_log)
        self.record_worker.finished_rec.connect(self._on_record_finished)
        self.record_worker.failed.connect(self._on_record_failed)
        self.record_worker.start()

    def start_recording(self) -> None:
        """录制当前输入的直播/视频地址。"""
        if self.record_worker is not None and self.record_worker.isRunning():
            QMessageBox.warning(self, "提示", "已有录制在进行中。")
            return
        url = self.record_url_edit.text().strip()
        if not url:
            QMessageBox.warning(self, "提示", "请输入直播或视频地址。")
            return
        self._begin_record(self._record_params(url))

    def stop_recording(self) -> None:
        """优雅停止录制（等待 yt-dlp 收尾，保留文件）。"""
        if self.record_worker is None or not self.record_worker.isRunning():
            return
        self.append_log("正在停止录制（等待 yt-dlp 收尾）…")
        self.btn_record_stop.setEnabled(False)
        self.record_worker.stop_recording()

    def save_record_snapshot(self) -> None:
        """把已录制的部分复制保存一份，不中断录制。"""
        if self.record_worker is None or not self.record_worker.isRunning():
            QMessageBox.warning(self, "提示", "当前没有正在进行的录制。")
            return
        copied = self.record_worker.save_snapshot()
        if copied:
            for path in copied:
                self.append_log(f"  已保存片段: {path}")
            self.summary_label.setText(
                f"已保存 {len(copied)} 个文件（录制未中断）"
            )
        else:
            self.summary_label.setText("尚无已录制内容可保存")

    def _on_record_finished(self, info: dict) -> None:
        """录制结束（自然结束或手动停止）：展示结果并询问弹幕处理。"""
        self.btn_record_start.setEnabled(True)
        self.btn_record_stop.setEnabled(False)
        self.btn_record_save.setEnabled(False)
        self.btn_monitor_start.setEnabled(True)
        self._set_status("等待任务", "idle")
        self.record_status_label.setText("未在录制")

        files = info.get("files", [])
        chat = info.get("chat")
        self.append_log(f"录制结束（退出码 {info.get('returncode')}），产出文件:")
        for f in files:
            self.append_log(f"  {f}")
        self.summary_label.setText(f"录制完成: {len(files)} 个文件")

        if chat is not None and chat.exists() and chat.stat().st_size > 0:
            answer = QMessageBox.question(
                self,
                "转换弹幕",
                "录制包含弹幕 JSON，是否转换为 XML 并加入任务队列？",
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._convert_chat_json_and_adopt(chat)

    def _on_record_failed(self, message: str) -> None:
        self.btn_record_start.setEnabled(True)
        self.btn_record_stop.setEnabled(False)
        self.btn_record_save.setEnabled(False)
        self.btn_monitor_start.setEnabled(True)
        self._set_status("录制失败", "error")
        self.append_log(f"录制失败: {message}")
        self.record_status_label.setText("未在录制")
        QMessageBox.critical(self, "录制失败", message)

    def _update_record_status(self) -> None:
        worker = self.record_worker
        if worker is None or not worker.isRunning() or worker.session is None:
            return
        elapsed = int(time.perf_counter() - self._record_started_at)
        size_mb = worker.session.total_size / (1024 * 1024)
        self.record_status_label.setText(
            f"录制中：已录 {format_duration(elapsed)}，当前文件 {size_mb:.1f} MB"
        )

    def start_monitor(self) -> None:
        """开始监控频道开播，开播时自动开始录制。"""
        if self.monitor_worker is not None and self.monitor_worker.isRunning():
            QMessageBox.warning(self, "提示", "监控已在进行中。")
            return
        url = self.record_url_edit.text().strip()
        if not url:
            QMessageBox.warning(self, "提示", "请输入频道链接或直播地址。")
            return
        try:
            recorder = load_config(self.config_edit.text().strip() or None).recorder
        except DanmakuStudioError:
            recorder = DEFAULT_CONFIG.recorder
        self.monitor_worker = MonitorWorker(
            url,
            recorder.monitor_interval,
            proxy=self.record_proxy_edit.text().strip(),
            pot_token_script=self.record_pot_edit.text().strip(),
        )
        self.monitor_worker.log.connect(self.append_log)
        self.monitor_worker.failed.connect(self._on_monitor_failed)
        self.monitor_worker.live_found.connect(self._on_monitor_live_found)
        self.monitor_worker.monitor_finished.connect(self._on_monitor_finished)
        self.btn_monitor_start.setEnabled(False)
        self.btn_monitor_stop.setEnabled(True)
        self._set_status("监控中", "ready")
        self.monitor_worker.start()

    def stop_monitor(self) -> None:
        if self.monitor_worker is not None:
            self.monitor_worker.stop_monitor()
            self.append_log("正在停止监控…")

    def _on_monitor_finished(self) -> None:
        self.btn_monitor_start.setEnabled(True)
        self.btn_monitor_stop.setEnabled(False)
        if self.record_worker is None or not self.record_worker.isRunning():
            self._set_status("等待任务", "idle")

    def _on_monitor_live_found(self, url: str, title: str) -> None:
        """监控到开播：自动开始录制（已有录制时跳过）。"""
        self.append_log(f"检测到直播开播: {title}（{url}）")
        if self.record_worker is not None and self.record_worker.isRunning():
            self.append_log("已有录制进行中，跳过自动录制。")
            return
        self._begin_record(self._record_params(url))

    def _on_monitor_failed(self, message: str) -> None:
        self.btn_monitor_start.setEnabled(True)
        self.btn_monitor_stop.setEnabled(False)
        self._set_status("监控失败", "error")
        self.append_log(f"监控失败: {message}")
        QMessageBox.critical(self, "监控失败", message)

    def _hug_content_page(self, card: QWidget) -> QWidget:
        """功能页容器：卡片贴内容高度，避免大片空白卡片。"""
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.addWidget(card)
        page_layout.addStretch(1)
        return page

    def _build_download_page(self) -> QWidget:
        """「下载内容」功能页：yt-dlp 下载视频 / 弹幕 / 封面。"""
        try:
            recorder_cfg = load_config(
                self.config_edit.text().strip() or None
            ).recorder
        except (DanmakuStudioError, AttributeError):
            recorder_cfg = DEFAULT_CONFIG.recorder
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(12)

        title = QLabel("下载视频 / 弹幕 / 封面")
        title.setObjectName("sectionTitle")
        description = QLabel(
            "用 yt-dlp 下载 YouTube 直播回放，可任意勾选组合；"
            "视频 + 弹幕下载完成后会自动转换为 XML 并询问加入任务队列。"
        )
        description.setObjectName("sectionDescription")
        description.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(description)

        form = QFormLayout()
        form.setSpacing(10)
        self.dl_url_edit = QLineEdit()
        self.dl_url_edit.setPlaceholderText("https://www.youtube.com/watch?v=...")
        form.addRow("视频链接", self.dl_url_edit)
        self.dl_format_edit = QLineEdit()
        self.dl_format_edit.setPlaceholderText("留空使用默认（优先 mp4）")
        self.dl_format_edit.setToolTip('yt-dlp 的 -f 格式字符串，例如 "bv*[height<=1080]+ba/b"')
        form.addRow("下载格式", self.dl_format_edit)
        self.dl_proxy_edit = QLineEdit(recorder_cfg.download_proxy)
        self.dl_proxy_edit.setPlaceholderText("留空 = 不使用代理")
        self.dl_proxy_edit.setToolTip(
            "yt-dlp --proxy，例如 http://127.0.0.1:7897；"
            "非空时会清理环境残留代理并让 ffmpeg 切片也走该代理"
        )
        form.addRow("代理", self.dl_proxy_edit)
        content_row = QHBoxLayout()
        content_row.setSpacing(12)
        self.dl_video_check = CheckMarkCheckBox("视频（直播回放）")
        self.dl_video_check.setChecked(True)
        self.dl_chat_check = CheckMarkCheckBox("实时弹幕（live_chat JSON）")
        self.dl_chat_check.setChecked(True)
        self.dl_thumbnail_check = CheckMarkCheckBox("封面（转 PNG）")
        for check in (self.dl_video_check, self.dl_chat_check, self.dl_thumbnail_check):
            content_row.addWidget(check)
        content_row.addStretch(1)
        form.addRow("下载内容", content_row)
        section_row = QHBoxLayout()
        section_row.setSpacing(8)
        self.dl_section_start_edit = QLineEdit()
        self.dl_section_start_edit.setPlaceholderText("如 5:01（留空 = 从开头）")
        self.dl_section_start_edit.setToolTip("片段起点，格式 分:秒 或 时:分:秒")
        section_row.addWidget(self.dl_section_start_edit)
        dash = QLabel("–")
        section_row.addWidget(dash)
        self.dl_section_end_edit = QLineEdit()
        self.dl_section_end_edit.setPlaceholderText("如 10:43（留空 = 到结尾）")
        self.dl_section_end_edit.setToolTip("片段终点，格式 分:秒 或 时:分:秒")
        section_row.addWidget(self.dl_section_end_edit)
        section_row.addStretch(1)
        form.addRow("下载片段", section_row)
        dir_row = QHBoxLayout()
        dir_row.setSpacing(8)
        self.dl_dir_edit = QLineEdit()
        self.dl_dir_edit.setPlaceholderText("保存目录")
        self.dl_dir_edit.setText(self.last_dir)
        self.btn_download_browse = QPushButton("浏览…")
        self.btn_download_browse.clicked.connect(self.browse_download_dir)
        dir_row.addWidget(self.dl_dir_edit, 1)
        dir_row.addWidget(self.btn_download_browse)
        form.addRow("保存目录", dir_row)
        layout.addLayout(form)

        download_row = QHBoxLayout()
        self.btn_download_start = QPushButton("开始下载")
        self.btn_download_start.clicked.connect(self.start_download)
        download_row.addWidget(self.btn_download_start)
        download_row.addStretch(1)
        layout.addLayout(download_row)
        return self._hug_content_page(card)

    def _update_clip_start_enabled(self, *_args) -> None:
        """自定义起点仅在与测试片段同时启用时可用。"""
        start_enabled = self.test_clip_check.isChecked() and self.clip_start_check.isChecked()
        self.clip_start_check.setEnabled(self.test_clip_check.isChecked())
        self.clip_start_spin.setEnabled(start_enabled)

    def choose_drop_first_reference(self) -> None:
        """参照选择删除条数：从开头的弹幕里保留一条，之前的全部删除。"""
        xml_path = self._reference_subtitle_path()
        if xml_path is None:
            return
        try:
            events = parse_danmaku_preview_events(xml_path)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "提示", f"弹幕文件读取失败: {e}")
            return
        if not events:
            QMessageBox.warning(self, "提示", "弹幕文件没有任何事件。")
            return

        dialog = TrimReferenceDialog(events, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.drop_first_spin.setValue(max(0, dialog.keep_index - 1))

    def _reference_subtitle_path(self):
        """参照选择使用的弹幕文件：优先任务队列第一个，否则手动选择。"""
        if self.pending_jobs:
            subtitle = self.pending_jobs[0].subtitle_path
            if not subtitle.exists():
                QMessageBox.warning(self, "提示", f"弹幕文件不存在: {subtitle}")
                return None
            return subtitle
        path_text, _ = QFileDialog.getOpenFileName(
            self,
            "选择弹幕 XML/LRC/JSON",
            self.last_dir,
            "弹幕文件 (*.xml *.lrc *.json);;所有文件 (*)",
        )
        return Path(path_text) if path_text else None

    def generate_preview_images(self) -> None:
        """生成预览图：选视频 → 匹配字幕 → 用当前界面设置渲染 PNG。"""
        if self._busy():
            QMessageBox.warning(self, "提示", "已有任务正在处理，请稍候。")
            return

        video_filter = (
            "视频文件 (*" + " *".join(sorted(SUPPORTED_VIDEO_EXTS)) + ");;所有文件 (*)"
        )
        video_text, _ = QFileDialog.getOpenFileName(
            self, "选择要预览的视频", self.last_dir, video_filter,
        )
        if not video_text:
            return
        video = Path(video_text)

        subtitle = find_matching_subtitle(video)
        if subtitle is None:
            answer = QMessageBox.question(
                self,
                "未找到同名字幕",
                f"未找到与 {video.name} 同名的 XML/LRC/JSON 弹幕文件。\n是否手动选择？",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            subtitle_text, _ = QFileDialog.getOpenFileName(
                self,
                "选择弹幕 XML/LRC/JSON",
                str(video.parent),
                "弹幕文件 (*.xml *.lrc *.json);;所有文件 (*)",
            )
            if not subtitle_text:
                return
            subtitle = Path(subtitle_text)

        try:
            config = self._effective_config()
        except DanmakuStudioError as e:
            QMessageBox.warning(self, "提示", f"无法加载当前配置: {e}")
            return

        self._remember_selection_dir([str(video)], str(video.parent))
        self.append_log("")
        self.append_log(f"生成预览图: {video.name} + {subtitle.name}")
        self.setCursor(Qt.CursorShape.WaitCursor)
        try:
            from ..core.preview import generate_previews

            paths = generate_previews(
                str(video), str(subtitle), config,
                out_dir=video.parent / "preview",
                drop_first=int(self.drop_first_spin.value()),
            )
        except DanmakuStudioError as e:
            QMessageBox.critical(self, "预览失败", f"[{e.category.value}] {e}")
            return
        except Exception as e:
            QMessageBox.critical(self, "预览失败", str(e))
            return
        finally:
            self.unsetCursor()

        self.log.clear()
        self.append_log("预览图已生成:")
        for p in paths:
            self.append_log(f"  {p}")
        self.summary_label.setText(f"已生成 {len(paths)} 张预览图")
        answer = QMessageBox.question(
            self,
            "打开预览图",
            f"已生成 {len(paths)} 张预览图，保存在:\n{video.parent / 'preview'}\n\n是否打开查看？",
        )
        if answer == QMessageBox.StandardButton.Yes:
            for p in paths:
                os.startfile(str(p))

    def _set_controls_locked(self, locked: bool) -> None:
        """任务运行期间锁定界面控件，结束后恢复。"""
        for widget in (
            self.btn_choose, self.btn_start, self.btn_config, self.encode_combo,
            self.force_checkbox, self.btn_preview,
            self.btn_drop_reference, self.drop_first_spin,
            self.test_clip_check, self.clip_duration_spin,
            self.clip_start_check, self.clip_start_spin,
            self.btn_json_convert, self.btn_highlights, self.btn_trim,
            self.btn_download_start, self.render_scale_spin, self.render_scale_spin,
        ):
            widget.setEnabled(not locked)
        if not locked:
            self._update_clip_start_enabled()

    def _effective_config(self) -> DanmakuConfig:
        """按界面当前设置构建有效配置（配置文件 + 样式覆盖 + 渲染精度 + 首条弹幕时间）。"""
        config = load_config(self.config_edit.text().strip() or None)
        overrides = self.style_panel.style_overrides()
        if overrides is not None:
            config = apply_style_overrides(config, overrides)
        config = replace(
            config,
            style=replace(config.style, render_scale=self.style_panel.render_scale_value()),
        )
        if self.first_danmaku_check.isChecked():
            config = replace(
                config,
                animation=replace(
                    config.animation,
                    first_danmaku_at=self.first_danmaku_spin.value(),
                ),
            )
        return config

    def save_style_to_yaml(self) -> None:
        """把界面样式合并进配置并保存为 YAML 文件。"""
        try:
            config = self._effective_config()
        except DanmakuStudioError as e:
            QMessageBox.warning(self, "提示", f"无法加载当前配置: {e}")
            return

        start_location = config_dialog_start_location(
            self.config_edit.text(),
            self.last_config_dir,
        )
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "保存样式到 YAML",
            start_location,
            "YAML 文件 (*.yaml *.yml);;所有文件 (*)",
        )
        if not file_path:
            return

        target = Path(file_path)
        if target.exists():
            answer = QMessageBox.question(
                self,
                "覆盖确认",
                f"保存将覆盖现有文件，且文件内注释不会被保留：\n{target}\n\n是否继续？",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        try:
            target.write_text(config_to_yaml(config), encoding="utf-8")
        except OSError as e:
            QMessageBox.warning(self, "提示", f"保存失败: {e}")
            return
        QMessageBox.information(self, "完成", f"已保存: {target}")

    def choose_config(self) -> None:
        start_location = config_dialog_start_location(
            self.config_edit.text(),
            self.last_config_dir,
        )
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择配置文件",
            start_location,
            "YAML 文件 (*.yaml *.yml);;所有文件 (*)",
        )
        if file_path:
            self.config_edit.setText(file_path)
            self.last_config_dir = os.path.dirname(file_path)
            self._save_last_config_dir(self.last_config_dir)
            self._reload_style_panel()

    def append_log(self, text: str) -> None:
        if text:
            self.log.append(text)
        else:
            self.log.append("")

    def choose_items(self) -> None:
        if self._busy():
            QMessageBox.warning(self, "提示", "已有任务正在处理，请稍候。")
            return

        dialog = FileFolderPickerDialog(self, self.last_dir)
        if dialog.exec() != QDialog.Accepted:
            return

        selected = dialog.selected_paths()
        self._remember_selection_dir(selected, dialog.current_directory())

        selection = collect_batch_jobs(selected)
        self.pending_jobs = []
        self.btn_start.setEnabled(False)
        self.log.clear()
        self.progress_bar.setValue(0)

        self.append_log("已选择:")
        for path in selected:
            self.append_log(f"  {path}")

        if selection.ignored_paths:
            self.append_log("")
            self.append_log("已忽略不支持的路径:")
            for path in selection.ignored_paths:
                self.append_log(f"  {path}")

        if selection.missing_subtitles:
            self.append_log("")
            self.append_log("未找到同名 XML/LRC 字幕的视频:")
            for path in selection.missing_subtitles:
                self.append_log(f"  {path}")

        if not selection.jobs:
            QMessageBox.warning(self, "提示", "没有找到可处理的视频和同名字幕。")
            self.summary_label.setText("没有可处理任务")
            self._set_status("未找到任务", "error")
            return

        self._adopt_jobs(selection.jobs)

    def _adopt_jobs(self, jobs: list[BatchJob]) -> None:
        """采纳一批任务：替换队列、展示任务列表并启用开始按钮。"""
        self.pending_jobs = jobs
        self.btn_start.setEnabled(True)
        self.progress_bar.setValue(0)
        self.append_log("")
        self.append_log("将处理:")
        for job in jobs:
            subtitle_kind = job.subtitle_path.suffix.upper().lstrip(".")
            self.append_log(
                f"  {job.video_path.name} + {subtitle_kind} {job.subtitle_path.name}"
            )
        self.summary_label.setText(
            f"已准备 {len(self.pending_jobs)} 个任务，点击开始处理"
        )
        self._set_status(f"{len(self.pending_jobs)} 个任务就绪", "ready")

    def browse_chat_json(self) -> None:
        path_text, _ = QFileDialog.getOpenFileName(
            self,
            "选择 YouTube 聊天 JSON",
            self.last_dir,
            "JSON 文件 (*.json);;所有文件 (*)",
        )
        if not path_text:
            return
        self.json_path_edit.setText(path_text)
        self._remember_selection_dir([path_text], self.last_dir)

    def start_json_convert(self) -> None:
        """把页面选择的聊天 JSON 转换为弹幕 XML。"""
        json_path = self._selected_chat_json()
        if json_path is None:
            return
        self._convert_chat_json_and_adopt(json_path)

    def _selected_chat_json(self):
        """校验并返回页面选择的聊天 JSON；无效时弹窗提示并返回 None。"""
        if self._busy():
            QMessageBox.warning(self, "提示", "已有任务正在处理，请稍候。")
            return None
        path_text = self.json_path_edit.text().strip()
        if not path_text:
            QMessageBox.warning(self, "提示", "请先选择聊天 JSON 文件。")
            return None
        json_path = Path(path_text)
        if not json_path.is_file():
            QMessageBox.warning(self, "提示", f"文件不存在: {json_path}")
            return None
        return json_path

    def analyze_chat_highlights(self) -> None:
        """按弹幕发送频率分析聊天 JSON，把高潮时间段写入日志。"""
        json_path = self._selected_chat_json()
        if json_path is None:
            return

        from ..core.highlight import (
            analyze_highlights,
            extract_chat_times,
            format_clock,
        )

        try:
            times = extract_chat_times(str(json_path))
        except OSError as e:
            QMessageBox.warning(self, "提示", f"聊天 JSON 读取失败: {e}")
            return
        if not times:
            QMessageBox.warning(self, "提示", "未从聊天 JSON 中提取到任何弹幕消息。")
            return

        top = self.highlight_top_spin.value()
        intervals = analyze_highlights(times, top=top)
        duration = format_clock(times[-1])
        self.log.clear()
        self.append_log(f"直播高潮点分析: {json_path.name}")
        self.append_log(f"  弹幕总数 {len(times)} 条，时间跨度 {duration}（相对首条弹幕）")
        if not intervals:
            self.append_log("  未发现高潮点（没有相对前后区段明显更密集的时段）。")
        else:
            self.append_log(
                "  高潮时间段（10 秒窗口；相对前后明显更密集，单段最长 5 分钟，"
                f"取前 {top} 名）:"
            )
            for rank, it in enumerate(intervals, start=1):
                self.append_log(
                    f"    #{rank}  {format_clock(it.start)} – {format_clock(it.end)}"
                    f"   峰值 {it.peak} 条/10s，合计 {it.count} 条"
                )
        self.summary_label.setText(
            f"高潮点分析完成: {len(intervals)} 段（详见日志）"
        )

    def trim_danmaku_file(self) -> None:
        """删除开头弹幕并直接导出文件（JSON 进出 JSON，XML 进出 XML）。"""
        if self._busy():
            QMessageBox.warning(self, "提示", "已有任务正在处理，请稍候。")
            return
        path_text = self.json_path_edit.text().strip()
        if not path_text:
            QMessageBox.warning(self, "提示", "请先在上方选择弹幕文件。")
            return
        src = Path(path_text)
        if not src.is_file():
            QMessageBox.warning(self, "提示", f"文件不存在: {src}")
            return
        if src.suffix.lower() not in (".json", ".xml"):
            QMessageBox.warning(self, "提示", "仅支持 JSON 或 XML 弹幕文件。")
            return

        dialog = TrimExportDialog(src, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        count, overwrite = dialog.values()
        target = src if overwrite else default_trimmed_path(src, count)

        self.setCursor(Qt.CursorShape.WaitCursor)
        try:
            if src.suffix.lower() == ".json":
                removed, kept = trim_chat_json_file(src, target, count)
            else:
                removed, kept = trim_subtitle_xml_file(src, target, count)
        except (ValueError, OSError) as e:
            QMessageBox.warning(self, "提示", f"删除开头弹幕失败: {e}")
            return
        finally:
            self.unsetCursor()

        self.log.clear()
        self.append_log(f"删除开头弹幕: {src.name}")
        self.append_log(f"  已删除开头 {removed} 条，剩余 {kept} 条")
        self.append_log(f"  输出文件: {target}")
        self.summary_label.setText(f"删除开头弹幕完成 → {target.name}")

    def _convert_chat_json_and_adopt(self, json_path: Path) -> None:
        """转换聊天 JSON 为 XML；检测到同名视频时询问加入任务队列。"""
        from ..input.chat2xml import convert_chat_json, default_xml_path_for

        # xxx.live_chat.json → xxx.xml（去掉 .live_chat 中缀，保证字幕可匹配）
        xml_out = default_xml_path_for(json_path)
        self.setCursor(Qt.CursorShape.WaitCursor)
        try:
            stats = convert_chat_json(str(json_path), xml_out, lead_in=0.0)
        except Exception as e:
            QMessageBox.critical(self, "转换失败", f"JSON 转 XML 失败:\n{e}")
            return
        finally:
            self.unsetCursor()

        self.log.clear()
        self.append_log(f"JSON 转换完成: {stats['messages']} 条弹幕")
        self.append_log(f"  弹幕 XML: {xml_out}")

        # 检测同目录同名视频，询问是否直接加入任务队列
        video = next(
            (
                xml_out.with_suffix(ext)
                for ext in ("", ".mp4", ".flv", ".mkv", ".avi", ".mov", ".ts", ".webm")
                if xml_out.with_suffix(ext).is_file() and ext
            ),
            None,
        )
        if video is not None:
            answer = QMessageBox.question(
                self,
                "加入任务队列",
                f"转换成功（{stats['messages']} 条弹幕）。\n"
                f"检测到同名视频:\n{video}\n\n是否加入任务队列？",
            )
            if answer == QMessageBox.StandardButton.Yes:
                from ..batch import default_output_path_for_video

                self._adopt_jobs([BatchJob(
                    video_path=video,
                    subtitle_path=xml_out,
                    output_path=default_output_path_for_video(video),
                )])
                return

        self.summary_label.setText(
            f"JSON 转换完成: {stats['messages']} 条弹幕 → {xml_out.name}"
        )

    def browse_download_dir(self) -> None:
        start = self.dl_dir_edit.text().strip() or self.last_dir
        dir_path = QFileDialog.getExistingDirectory(self, "选择保存目录", start)
        if dir_path:
            self.dl_dir_edit.setText(dir_path)
            self._remember_selection_dir([dir_path], self.last_dir)

    def _download_format(self) -> str:
        """页面上的 yt-dlp 下载格式；空字符串表示使用默认。"""
        return self.dl_format_edit.text().strip()

    def start_download(self) -> None:
        """按页面设置在后台用 yt-dlp 下载视频 / 弹幕 / 封面。"""
        if self._busy():
            QMessageBox.warning(self, "提示", "已有任务正在处理，请稍候。")
            return
        url = self.dl_url_edit.text().strip()
        if not url:
            QMessageBox.warning(self, "提示", "请输入视频链接。")
            return
        want_video = self.dl_video_check.isChecked()
        want_chat = self.dl_chat_check.isChecked()
        want_thumbnail = self.dl_thumbnail_check.isChecked()
        if not (want_video or want_chat or want_thumbnail):
            QMessageBox.warning(self, "提示", "请至少勾选一项要下载的内容。")
            return

        from ..core.pipeline import _YTDLP_FORMAT

        out_dir = self.dl_dir_edit.text().strip() or self.last_dir
        section = None
        start_text = self.dl_section_start_edit.text().strip()
        end_text = self.dl_section_end_edit.text().strip()
        if start_text or end_text:
            time_re = re.compile(r"^\d{1,2}(:\d{2}){0,2}$")
            if (start_text and not time_re.match(start_text)) or (
                end_text and not time_re.match(end_text)
            ):
                QMessageBox.warning(
                    self, "提示",
                    "片段时间格式应为 分:秒 或 时:分:秒（如 5:01、1:02:30）。"
                )
                return
            section = f"*{start_text or ''}-{end_text or ''}"
        self.btn_download_start.setEnabled(False)
        self._set_status("正在下载", "busy")
        self.append_log("")
        self.append_log(f"开始下载: {url}（后台进行，进度见下方日志）")
        if section:
            self.append_log(
                "提示：指定片段需从视频开头顺序读取到片段起点，"
                "长视频可能耗时较长，请以「下载完成」日志为准。"
            )
        self.download_worker = DownloadWorker(
            url,
            self._download_format() or _YTDLP_FORMAT,
            out_dir,
            want_video=want_video,
            want_chat=want_chat,
            want_thumbnail=want_thumbnail,
            section=section,
            proxy=self.dl_proxy_edit.text().strip(),
        )
        self.download_worker.log.connect(self.append_log)
        self.download_worker.succeeded.connect(self._on_download_succeeded)
        self.download_worker.failed.connect(self._on_download_failed)
        self.download_worker.start()

    def _on_download_succeeded(self, result: dict) -> None:
        """下载完成（主线程）：展示结果并处理后续（转 XML / 加入队列 / 打开封面）。"""
        self.btn_download_start.setEnabled(True)
        self._set_status("等待任务", "idle")
        out_dir = (
            Path(self.download_worker.out_dir) if self.download_worker else Path.cwd()
        )

        video = result.get("video")
        chat = result.get("chat")
        thumbnail = result.get("thumbnail")

        self.append_log("下载完成:")
        if video is not None:
            self.append_log(f"  视频: {video.name}")
        if chat is not None:
            self.append_log(f"  弹幕 JSON: {chat.name}")
        if thumbnail is not None:
            self.append_log(f"  封面: {thumbnail.name}")
        self.summary_label.setText(
            "下载完成: "
            + " / ".join(p.name for p in (video, chat, thumbnail) if p is not None)
        )

        if video is not None:
            self._remember_selection_dir([str(video)], str(out_dir))
        elif thumbnail is not None:
            self._remember_selection_dir([str(thumbnail)], str(out_dir))

        # 视频 + 弹幕：先（可选）删除 JSON 开头弹幕，再转 XML 并询问加入队列
        if video is not None and chat is not None:
            from ..input.chat2xml import convert_chat_json

            xml_out = video.with_suffix(".xml")
            drop_first = self._ask_drop_first_for_json(chat)
            stats = convert_chat_json(
                str(chat), xml_out, lead_in=0.0, drop_first=drop_first,
            )
            if drop_first:
                self.append_log(f"  已删除开头 {drop_first} 条弹幕")
            self.append_log(f"  弹幕转换: {stats['messages']} 条 → {xml_out.name}")
            answer = QMessageBox.question(
                self,
                "加入任务队列",
                f"下载并转换完成（{stats['messages']} 条弹幕）。\n\n是否加入任务队列？",
            )
            if answer == QMessageBox.StandardButton.Yes:
                from ..batch import default_output_path_for_video

                self._adopt_jobs([BatchJob(
                    video_path=video,
                    subtitle_path=xml_out,
                    output_path=default_output_path_for_video(video),
                )])

        # 封面：询问是否打开
        if thumbnail is not None:
            answer = QMessageBox.question(
                self,
                "下载完成",
                f"封面已保存为 PNG:\n{thumbnail}\n\n是否打开查看？",
            )
            if answer == QMessageBox.StandardButton.Yes:
                os.startfile(str(thumbnail))

    def _ask_drop_first_for_json(self, chat_json: Path) -> int:
        """下载完成后询问：转换前是否删除 JSON 开头弹幕。

        参照「压制设置 → 删除开头弹幕」的交互：列出 JSON 开头的弹幕
        （序号/时间/用户/内容）选择保留哪一条，其之前的全部删除。
        返回要删除的条数（0 = 不删除）。
        """
        from ..input.chat2xml import preview_json_events

        try:
            events = preview_json_events(str(chat_json))
        except OSError as e:
            QMessageBox.warning(self, "提示", f"聊天 JSON 读取失败: {e}")
            return 0
        if not events:
            return 0

        answer = QMessageBox.question(
            self,
            "删除开头弹幕",
            "转换前是否先删除开头几条弹幕（如直播开始前发出的测试弹幕）？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return 0

        dialog = TrimReferenceDialog(events, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return 0
        return max(0, dialog.keep_index - 1)

    def _on_download_failed(self, message: str) -> None:
        """下载失败（主线程）：恢复按钮并提示。"""
        self.btn_download_start.setEnabled(True)
        self._set_status("下载失败", "error")
        self.append_log(f"下载失败: {message}")
        QMessageBox.critical(self, "下载失败", message)

    def _remember_selection_dir(self, selected: list[str], fallback: str) -> None:
        selected_for_last_dir = selected[0] if selected else fallback
        current_dir = (
            selected_for_last_dir
            if os.path.isdir(selected_for_last_dir)
            else os.path.dirname(selected_for_last_dir)
        )
        if os.path.isdir(current_dir):
            self.last_dir = current_dir
            self._save_last_dir(self.last_dir)

    def start_pending_jobs(self) -> None:
        if self._busy():
            QMessageBox.warning(self, "提示", "已有任务正在处理，请稍候。")
            return

        if not self.pending_jobs:
            QMessageBox.warning(self, "提示", "请先选择可处理的视频和字幕。")
            return

        if not self._confirm_start(list(self.pending_jobs)):
            return

        self.start_worker(list(self.pending_jobs))

    def _confirm_start(self, jobs: list[BatchJob]) -> bool:
        """压制前弹出确认框，二次核对当前全部配置。"""
        lines: list[str] = [f"任务数量：{len(jobs)}"]
        for job in jobs[:5]:
            lines.append(f"  · {job.video_path.name}")
        if len(jobs) > 5:
            lines.append(f"  · …等 {len(jobs)} 个视频")
        lines.append("")

        config_path = self.config_edit.text().strip() or "（默认内置配置）"
        lines.append(f"配置文件：{config_path}")
        lines.append(f"编码模式：{self.encode_combo.currentText()}")
        lines.append(
            f"覆盖已存在输出：{'是' if self.force_checkbox.isChecked() else '否'}"
        )
        if self.first_danmaku_check.isChecked():
            lines.append(f"首条弹幕时间：{self.first_danmaku_spin.value():g} 秒")
        else:
            lines.append("首条弹幕时间：不调整")
        drop_first = int(self.drop_first_spin.value())
        lines.append(
            f"删除开头弹幕：前 {drop_first} 条" if drop_first else "删除开头弹幕：不删除"
        )
        ffmpeg_args = self.ffmpeg_args_edit.text().strip()
        lines.append(f"FFmpeg 自定义参数：{ffmpeg_args or '（无）'}")
        if self.test_clip_check.isChecked():
            if self.clip_start_check.isChecked():
                lines.append(
                    f"只导出测试片段：从 {self.clip_start_spin.value():g} 秒开始，"
                    f"时长 {self.clip_duration_spin.value():g} 秒"
                )
            else:
                lines.append(
                    f"只导出测试片段：从首条弹幕处开始，时长 {self.clip_duration_spin.value():g} 秒"
                )
        else:
            lines.append("只导出测试片段：否（压制完整视频）")

        if self.style_panel.is_custom():
            style = self.style_panel.current_style()
            font_desc = style.font_family or "默认（内置回退字体）"
            lock_desc = "已锁定" if self.style_panel.font_lock_check.isChecked() else "未锁定"
            lines.append("自定义样式：已启用")
            lines.append(f"  字体：{font_desc}（{lock_desc}）")
            lines.append(f"  字号：{style.font_size} pt")
            lines.append(f"  普通文本色：{style.text.color}")
            lines.append(f"  会员文本色：{style.member_text.color}")
        else:
            lines.append("自定义样式：未启用（跟随配置文件）")

        answer = QMessageBox.question(
            self,
            "确认压制配置",
            "请确认以下配置无误后再开始压制：\n\n" + "\n".join(lines),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def start_worker(self, jobs: list[BatchJob]) -> None:
        config_path = self.config_edit.text().strip() or None
        self._set_controls_locked(True)
        self.summary_label.setText(f"处理中: 0/{len(jobs)}")
        self.progress_bar.setValue(0)
        self._set_status("正在处理", "busy")
        self.append_log("")
        self.append_log("开始处理...")

        first_danmaku_at = (
            self.first_danmaku_spin.value()
            if self.first_danmaku_check.isChecked()
            else None
        )
        test_clip = self.test_clip_check.isChecked()
        self.worker = BatchWorker(
            jobs=jobs,
            encode_mode=self.encode_combo.currentData(),
            config_path=config_path,
            force=self.force_checkbox.isChecked(),
            style_overrides=self.style_panel.style_overrides(),
            first_danmaku_at=first_danmaku_at,
            ffmpeg_args=self.ffmpeg_args_edit.text().strip() or None,
            test_clip=test_clip,
            clip_duration=self.clip_duration_spin.value(),
            drop_first=int(self.drop_first_spin.value()),
            render_scale=self.render_scale_spin.value(),
            clip_start=(
                self.clip_start_spin.value()
                if test_clip and self.clip_start_check.isChecked()
                else None
            ),
        )
        self.worker.log.connect(self.append_log)
        self.worker.progress.connect(self.on_worker_progress)
        self.worker.summary.connect(self.on_worker_summary)
        self.worker.start()

    def on_worker_progress(
        self,
        task_index: int,
        task_total: int,
        percent: float,
        elapsed: float,
    ) -> None:
        overall_percent = (
            (max(1, task_index) - 1) + max(0.0, min(100.0, percent)) / 100.0
        ) / max(1, task_total)
        self.progress_bar.setValue(round(overall_percent * self.progress_bar.maximum()))
        self.summary_label.setText(
            f"处理中: 任务 {task_index}/{task_total} | "
            f"{percent:5.1f}% | 已用 {format_duration(elapsed)}"
        )

    def on_worker_summary(
        self,
        total: int,
        generated: int,
        skipped: int,
        failed: int,
    ) -> None:
        self._set_controls_locked(False)
        self.btn_start.setEnabled(False)
        self.pending_jobs = []
        self.worker = None
        self.progress_bar.setValue(self.progress_bar.maximum())

        self.summary_label.setText(
            f"完成: 共 {total} 个，成功 {generated} 个，跳过 {skipped} 个，失败 {failed} 个"
        )
        if failed:
            self._set_status(f"完成 · {failed} 个失败", "error")
        else:
            self._set_status("全部完成", "done")
        QMessageBox.information(
            self,
            "处理完成",
            (
                f"共 {total} 个任务\n"
                f"成功 {generated} 个\n"
                f"跳过 {skipped} 个\n"
                f"失败 {failed} 个"
            ),
            QMessageBox.Ok,
        )
