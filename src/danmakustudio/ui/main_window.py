"""DanmakuStudio 主窗口。"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..batch import BatchJob, collect_batch_jobs
from ..config.loader import config_to_yaml, load_config
from ..config.models import EncodeMode, apply_style_overrides
from ..errors import DanmakuStudioError
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
from .worker import BatchWorker


class DownloadDialog(QDialog):
    """统一下载对话框：输入链接 + 勾选要下载的内容（视频/弹幕/封面）。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("下载视频 / 弹幕 / 封面")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        form = QFormLayout()
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://www.youtube.com/watch?v=...")
        form.addRow("视频链接", self.url_edit)
        layout.addLayout(form)

        self.video_check = QCheckBox("视频（直播回放）")
        self.video_check.setChecked(True)
        self.chat_check = QCheckBox("实时弹幕（live_chat JSON）")
        self.chat_check.setChecked(True)
        self.thumbnail_check = QCheckBox("封面（转 PNG）")
        layout.addWidget(self.video_check)
        layout.addWidget(self.chat_check)
        layout.addWidget(self.thumbnail_check)

        hint = QLabel("可任意勾选组合；下载视频与弹幕后会自动转 XML 并询问是否加入队列。")
        hint.setObjectName("fieldHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("下载")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def url(self) -> str:
        return self.url_edit.text().strip()


class DanmakuStudioWindow(QWidget):
    """DanmakuStudio 主窗口。"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("DanmakuStudio · 弹幕压制工作台")
        self.setObjectName("appRoot")
        self.resize(920, 880)
        self.setMinimumSize(760, 680)
        self.worker: BatchWorker | None = None
        self.pending_jobs: list[BatchJob] = []
        self.last_dir = str(get_app_dir())
        self.last_config_dir = ""
        self._load_last_dir()
        self._load_last_config_dir()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(16)

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

        first_label = QLabel("首条弹幕时间")
        first_label.setObjectName("fieldLabel")
        options.addWidget(first_label, 2, 0)
        first_row = QHBoxLayout()
        first_row.setSpacing(8)
        self.first_danmaku_check = QCheckBox("调整")
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
        self.test_clip_check = QCheckBox("只导出测试片段")
        self.test_clip_check.setToolTip("勾选后每个任务只压制一个片段，用于先确认效果")
        self.clip_duration_spin = QDoubleSpinBox()
        self.clip_duration_spin.setRange(1.0, 3600.0)
        self.clip_duration_spin.setSingleStep(10.0)
        self.clip_duration_spin.setDecimals(0)
        self.clip_duration_spin.setValue(60.0)
        self.clip_duration_spin.setSuffix(" 秒")
        self.clip_duration_spin.setToolTip("测试片段时长，从首条弹幕处开始")
        clip_row.addWidget(self.test_clip_check)
        clip_row.addWidget(self.clip_duration_spin)
        clip_row.addStretch(1)
        options.addLayout(clip_row, 4, 1)
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
        style_layout.addWidget(self.style_panel)

        # 上方两张设置卡片放入滚动区：窗口高度不足时滚动而非压缩，
        # 避免样式面板各行被挤压重叠
        scroll = QScrollArea()
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_body = QWidget()
        scroll_body.setObjectName("scrollBody")
        scroll_layout = QVBoxLayout(scroll_body)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(16)
        scroll_layout.addWidget(settings_card)
        scroll_layout.addWidget(style_card)
        scroll_layout.addStretch(1)
        scroll.setWidget(scroll_body)
        layout.addWidget(scroll, 0)

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

        self.btn_import_json = QPushButton("JSON 转 XML…")
        self.btn_import_json.setToolTip("把 YouTube 直播回放聊天 JSON 转换为弹幕 XML")
        self.btn_import_json.clicked.connect(self.import_chat_json)
        actions.addWidget(self.btn_import_json)

        self.btn_download = QPushButton("下载视频/弹幕/封面…")
        self.btn_download.setToolTip("用 yt-dlp 下载 YouTube 视频、实时弹幕 JSON 与封面（可勾选组合）")
        self.btn_download.clicked.connect(self.download_flow)
        actions.addWidget(self.btn_download)

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
        layout.addWidget(task_card, 1)

        self._reload_style_panel()
        self.style_panel.restore_state()

    def closeEvent(self, event) -> None:
        self.style_panel.save_state()
        super().closeEvent(event)

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

    def save_style_to_yaml(self) -> None:
        """把界面样式合并进配置并保存为 YAML 文件。"""
        path_text = self.config_edit.text().strip() or None
        try:
            config = load_config(path_text)
        except DanmakuStudioError as e:
            QMessageBox.warning(self, "提示", f"无法加载当前配置: {e}")
            return
        overrides = self.style_panel.style_overrides()
        if overrides is not None:
            config = apply_style_overrides(config, overrides)
        if self.first_danmaku_check.isChecked():
            config = replace(
                config,
                animation=replace(
                    config.animation,
                    first_danmaku_at=self.first_danmaku_spin.value(),
                ),
            )

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
        if self.worker is not None and self.worker.isRunning():
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

    def import_chat_json(self) -> None:
        """把 YouTube 聊天 JSON 转换为弹幕 XML；检测到同名视频时询问加入任务队列。"""
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.warning(self, "提示", "已有任务正在处理，请稍候。")
            return

        json_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 YouTube 聊天 JSON",
            self.last_dir,
            "JSON 文件 (*.json);;所有文件 (*)",
        )
        if not json_path:
            return

        xml_out = Path(json_path).with_suffix(".xml")
        self.setCursor(Qt.CursorShape.WaitCursor)
        try:
            from ..input.chat2xml import convert_chat_json

            stats = convert_chat_json(json_path, xml_out, lead_in=0.0)
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
                from ..batch import BatchJob, default_output_path_for_video

                job = BatchJob(
                    video_path=video,
                    subtitle_path=xml_out,
                    output_path=default_output_path_for_video(video),
                )
                self._adopt_jobs([job])
                return

        self.summary_label.setText(
            f"JSON 转换完成: {stats['messages']} 条弹幕 → {xml_out.name}"
        )

    def download_flow(self) -> None:
        """统一下载：视频 / 实时弹幕 / 封面，可任意勾选组合。"""
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.warning(self, "提示", "已有任务正在处理，请稍候。")
            return

        dialog = DownloadDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        url = dialog.url()
        if not url:
            QMessageBox.warning(self, "提示", "请输入视频链接。")
            return
        want_video = dialog.video_check.isChecked()
        want_chat = dialog.chat_check.isChecked()
        want_thumbnail = dialog.thumbnail_check.isChecked()
        if not (want_video or want_chat or want_thumbnail):
            QMessageBox.warning(self, "提示", "请至少勾选一项要下载的内容。")
            return

        out_dir = QFileDialog.getExistingDirectory(self, "选择保存目录", self.last_dir)
        if not out_dir:
            return

        self.append_log(f"开始下载: {url}（请稍候，大视频耗时较长）")
        self.setCursor(Qt.CursorShape.WaitCursor)
        try:
            from ..core.pipeline import _YTDLP_FORMAT, download_items

            result = download_items(
                url, Path(out_dir), _YTDLP_FORMAT,
                want_video=want_video,
                want_chat=want_chat,
                want_thumbnail=want_thumbnail,
            )
        except Exception as e:
            self.unsetCursor()
            QMessageBox.critical(self, "下载失败", str(e))
            return
        finally:
            self.unsetCursor()

        video = result["video"]
        chat = result["chat"]
        thumbnail = result["thumbnail"]

        self.log.clear()
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
            self._remember_selection_dir([str(video)], out_dir)
        elif thumbnail is not None:
            self._remember_selection_dir([str(thumbnail)], out_dir)

        # 视频 + 弹幕：转 XML 并询问是否加入队列
        if video is not None and chat is not None:
            from ..input.chat2xml import convert_chat_json

            xml_out = video.with_suffix(".xml")
            stats = convert_chat_json(chat, xml_out, lead_in=0.0)
            self.append_log(f"  弹幕转换: {stats['messages']} 条 → {xml_out.name}")
            answer = QMessageBox.question(
                self,
                "加入任务队列",
                f"下载并转换完成（{stats['messages']} 条弹幕）。\n\n是否加入任务队列？",
            )
            if answer == QMessageBox.StandardButton.Yes:
                from ..batch import BatchJob, default_output_path_for_video

                job = BatchJob(
                    video_path=video,
                    subtitle_path=xml_out,
                    output_path=default_output_path_for_video(video),
                )
                self._adopt_jobs([job])

        # 封面：询问是否打开
        if thumbnail is not None:
            answer = QMessageBox.question(
                self,
                "下载完成",
                f"封面已保存为 PNG:\n{thumbnail}\n\n是否打开查看？",
            )
            if answer == QMessageBox.StandardButton.Yes:
                os.startfile(str(thumbnail))

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
        if self.worker is not None and self.worker.isRunning():
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
        ffmpeg_args = self.ffmpeg_args_edit.text().strip()
        lines.append(f"FFmpeg 自定义参数：{ffmpeg_args or '（无）'}")
        if self.test_clip_check.isChecked():
            lines.append(f"只导出测试片段：{self.clip_duration_spin.value():g} 秒")
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
        self.btn_choose.setEnabled(False)
        self.btn_start.setEnabled(False)
        self.btn_config.setEnabled(False)
        self.encode_combo.setEnabled(False)
        self.force_checkbox.setEnabled(False)
        self.btn_import_json.setEnabled(False)
        self.btn_download.setEnabled(False)
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
        self.worker = BatchWorker(
            jobs=jobs,
            encode_mode=self.encode_combo.currentData(),
            config_path=config_path,
            force=self.force_checkbox.isChecked(),
            style_overrides=self.style_panel.style_overrides(),
            first_danmaku_at=first_danmaku_at,
            ffmpeg_args=self.ffmpeg_args_edit.text().strip() or None,
            test_clip=self.test_clip_check.isChecked(),
            clip_duration=self.clip_duration_spin.value(),
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
        self.btn_choose.setEnabled(True)
        self.btn_config.setEnabled(True)
        self.encode_combo.setEnabled(True)
        self.force_checkbox.setEnabled(True)
        self.btn_import_json.setEnabled(True)
        self.btn_download.setEnabled(True)
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
