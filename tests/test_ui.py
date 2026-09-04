"""GUI 主题和主窗口状态测试。"""

from itertools import pairwise

import pytest
from PySide6.QtWidgets import QApplication

from danmakustudio.config import DEFAULT_CONFIG
from danmakustudio.config.models import (
    DanmakuConfig,
    EncodeMode,
    LayoutStyle,
    TextStyle,
)
from danmakustudio.ui._shared import config_dialog_start_location
from danmakustudio.ui.file_picker import FileFolderPickerDialog
from danmakustudio.ui.main_window import DanmakuStudioWindow, TrimReferenceDialog
from danmakustudio.ui.style_panel import StylePanel
from danmakustudio.ui.theme import APP_STYLESHEET, CheckMarkCheckBox, apply_theme


@pytest.fixture(autouse=True)
def _isolate_gui_state(monkeypatch, tmp_path):
    """隔离 GUI 状态文件，避免测试污染用户真实配置。"""
    monkeypatch.setattr(
        "danmakustudio.ui.style_panel.gui_style_path",
        lambda: tmp_path / "gui_style.json",
    )


def test_apply_theme_sets_application_stylesheet(qapp):
    assert isinstance(qapp, QApplication)

    apply_theme(qapp)

    assert qapp.style() is not None
    assert qapp.styleSheet() == APP_STYLESHEET


def test_main_window_initial_state(qapp):
    apply_theme(qapp)
    window = DanmakuStudioWindow()

    assert window.status_badge.text() == "等待任务"
    assert window.status_badge.property("state") == "idle"
    assert window.encode_combo.currentData() == EncodeMode.AUTO
    assert isinstance(window.force_checkbox, CheckMarkCheckBox)
    assert window.windowTitle() == "DanmakuStudio · 弹幕压制工作台"
    assert not window.windowIcon().isNull()
    assert window.btn_choose.text() == "选择视频或文件夹"
    assert not window.btn_start.isEnabled()
    assert window.progress_bar.value() == 0

    window.close()


def test_file_picker_uses_styled_controls(qapp, tmp_path):
    apply_theme(qapp)
    dialog = FileFolderPickerDialog(start_dir=str(tmp_path))

    assert dialog.tree.objectName() == "fileTree"
    assert dialog.tree.alternatingRowColors()
    assert dialog.windowTitle() == "选择视频或文件夹"
    assert dialog.btn_current.text() == "＋  使用当前文件夹"
    assert dialog.path_label.objectName() == "pathButton"

    dialog.close()


def test_config_dialog_prefers_selected_file_then_last_directory(tmp_path):
    selected = tmp_path / "selected.yaml"
    selected.write_text("style: {}", encoding="utf-8")
    previous = tmp_path / "previous"
    previous.mkdir()

    assert config_dialog_start_location(str(selected), str(previous)) == str(selected)
    assert config_dialog_start_location("", str(previous)) == str(previous)


def test_main_window_reports_overall_batch_progress(qapp):
    window = DanmakuStudioWindow()

    window.on_worker_progress(
        task_index=2,
        task_total=4,
        percent=20.0,
        elapsed=65.0,
    )

    assert window.progress_bar.value() == 300
    assert "任务 2/4" in window.summary_label.text()
    assert "20.0%" in window.summary_label.text()
    assert "01:05" in window.summary_label.text()

    window.close()


# =============================================================================
# 样式面板
# =============================================================================


def test_style_panel_default_state(qapp):
    apply_theme(qapp)
    panel = StylePanel()

    assert not panel.custom_check.isChecked()
    assert panel.style_overrides() is None
    assert not panel.content.isEnabled()
    assert panel.tabs.count() == 3

    panel.close()


def test_style_panel_loads_and_builds_overrides(qapp):
    apply_theme(qapp)
    panel = StylePanel()
    panel.load_from(DEFAULT_CONFIG)
    panel.custom_check.setChecked(True)

    overrides = panel.style_overrides()
    assert overrides is not None
    assert overrides.font_size == DEFAULT_CONFIG.style.font_size
    assert overrides.text.color == DEFAULT_CONFIG.style.text.color

    # 修改控件后 overrides 应反映新值
    panel.text_controls.color_btn.set_color_hex("#FF0000")
    panel.text_controls.opacity_spin.setValue(0.5)
    overrides = panel.style_overrides()
    assert overrides is not None
    assert overrides.text.color == "#FF0000"
    assert overrides.text.opacity == 0.5

    panel.close()


def test_style_panel_disabled_effects_yield_empty(qapp):
    """描边/阴影被关闭的配置，勾选后对应字段应为空/0。"""
    apply_theme(qapp)
    panel = StylePanel()
    plain_style = LayoutStyle(text=TextStyle(), member_text=TextStyle(color="#45dd7f"))
    panel.load_from(DanmakuConfig(style=plain_style))
    panel.custom_check.setChecked(True)

    overrides = panel.style_overrides()
    assert overrides is not None
    assert overrides.text.outline_color == ""
    assert overrides.text.outline_width == 0.0
    assert overrides.text.shadow_color == ""
    assert overrides.text.shadow_opacity == 0.0

    panel.close()


def test_json_page_converts_and_adopts_job(qapp, tmp_path, monkeypatch):
    """JSON 页转换应生成同名 XML；检测到同名视频时询问并加入任务队列。"""
    from PySide6.QtWidgets import QMessageBox

    video = tmp_path / "直播.mp4"
    video.write_bytes(b"not-a-real-video")
    chat = tmp_path / "直播.json"
    chat.write_text(
        '{"replayChatItemAction": {"actions": [{"addChatItemAction": '
        '{"item": {"liveChatTextMessageRenderer": {"timestampUsec": '
        '"1753357638681000", "authorName": {"simpleText": "u1"}, '
        '"authorBadges": [], "message": {"runs": [{"text": "hi"}]}}}}}]}}\n',
        encoding="utf-8",
    )

    apply_theme(qapp)
    window = DanmakuStudioWindow()
    window.json_path_edit.setText(str(chat))
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )

    window.start_json_convert()

    xml = tmp_path / "直播.xml"
    assert xml.exists()
    assert len(window.pending_jobs) == 1
    job = window.pending_jobs[0]
    assert job.video_path == video
    assert job.subtitle_path == xml
    assert window.btn_start.isEnabled()

    window.close()


def test_style_panel_reset_to_default(qapp):
    """重置按钮应把控件恢复为内置默认样式。"""
    apply_theme(qapp)
    panel = StylePanel()
    panel.load_from(DEFAULT_CONFIG)
    panel.custom_check.setChecked(True)
    panel.text_controls.color_btn.set_color_hex("#FF0000")
    panel.emoji_spacing_spin.setValue(10)
    assert panel.style_overrides().text.color == "#FF0000"

    panel.reset_btn.click()

    overrides = panel.style_overrides()
    assert overrides is not None
    assert overrides.text.color == DEFAULT_CONFIG.style.text.color
    assert overrides.text.opacity == DEFAULT_CONFIG.style.text.opacity
    assert overrides.emoji.spacing == DEFAULT_CONFIG.style.emoji.spacing

    panel.close()


def test_style_panel_state_roundtrip(qapp):
    """面板状态应能持久化并在新面板上恢复。"""
    apply_theme(qapp)
    panel = StylePanel()
    panel.load_from(DEFAULT_CONFIG)
    panel.custom_check.setChecked(True)
    panel.text_controls.color_btn.set_color_hex("#123456")
    panel.emoji_spacing_spin.setValue(9)
    panel.preview_bg_btn.set_color_hex("#3FA0A0")
    panel.save_state()

    panel2 = StylePanel()
    panel2.restore_state()
    assert panel2.custom_check.isChecked()
    assert panel2.text_controls.color_btn.color_hex() == "#123456"
    assert panel2.emoji_spacing_spin.value() == 9
    assert panel2.font_size_spin.value() == panel.font_size_spin.value()
    assert panel2.preview_bg_btn.color_hex() == "#3FA0A0"

    panel.close()
    panel2.close()


def test_style_preview_bg_default_and_custom(qapp):
    """预览背景默认灰色，可自定义且实时生效。"""
    apply_theme(qapp)
    panel = StylePanel()
    panel.load_from(DEFAULT_CONFIG)
    panel.custom_check.setChecked(True)
    assert panel.preview_bg_btn.color_hex() == "#808080"  # 默认灰

    panel.resize(700, 900)
    panel.show()
    qapp.processEvents()
    panel.preview_bg_btn.set_color_hex("#3FA0A0")
    img = panel.preview.grab().toImage()
    # QColor.name() 输出小写
    assert img.pixelColor(5, 5).name() == "#3fa0a0"

    panel.close()


def test_style_preview_paints_content(qapp):
    """实时预览应渲染出示例弹幕内容。"""
    apply_theme(qapp)
    panel = StylePanel()
    panel.load_from(DEFAULT_CONFIG)
    panel.custom_check.setChecked(True)
    panel.resize(700, 900)
    panel.show()
    qapp.processEvents()

    img = panel.preview.grab().toImage()
    assert not img.isNull()
    assert img.width() > 0 and img.height() > 0
    bg = img.pixelColor(3, 3)
    differs = any(
        img.pixelColor(x, y) != bg
        for x in range(0, img.width(), 6)
        for y in range(0, img.height(), 6)
    )
    assert differs, "预览区域未绘制任何内容"

    panel.close()


def test_style_panel_member_and_emoji_overrides(qapp):
    """会员与表情页应参与 overrides 生成。"""
    apply_theme(qapp)
    panel = StylePanel()
    panel.load_from(DEFAULT_CONFIG)
    panel.custom_check.setChecked(True)

    overrides = panel.style_overrides()
    assert overrides is not None
    assert overrides.member_text.color == DEFAULT_CONFIG.style.member_text.color
    assert overrides.emoji.opacity == DEFAULT_CONFIG.style.emoji.opacity
    assert overrides.emoji.size_ratio == DEFAULT_CONFIG.style.emoji.size_ratio
    assert overrides.emoji.spacing == DEFAULT_CONFIG.style.emoji.spacing

    panel.close()


def test_main_window_first_danmaku_controls(qapp):
    """首条弹幕时间控件：默认未勾选、默认值 2.0 秒。"""
    apply_theme(qapp)
    window = DanmakuStudioWindow()

    assert not window.first_danmaku_check.isChecked()
    assert window.first_danmaku_spin.value() == 2.0
    assert window.first_danmaku_spin.minimum() == 0.0

    window.close()


def test_style_panel_copy_text_to_member(qapp):
    """复制普通样式应把普通页值拷入会员页。"""
    apply_theme(qapp)
    panel = StylePanel()
    panel.load_from(DEFAULT_CONFIG)
    panel.text_controls.color_btn.set_color_hex("#FF0000")
    panel.text_controls.opacity_spin.setValue(0.5)

    panel.copy_text_to_member()

    member_ts = panel.member_controls.text_style()
    assert member_ts.color == "#FF0000"
    assert member_ts.opacity == 0.5

    panel.close()


def test_style_panel_member_controls_modify_overrides(qapp):
    """修改会员页控件应反映到 overrides。"""
    apply_theme(qapp)
    panel = StylePanel()
    panel.load_from(DEFAULT_CONFIG)
    panel.custom_check.setChecked(True)
    panel.member_controls.color_btn.set_color_hex("#45dd7f")
    panel.emoji_size_spin.setValue(0.8)

    overrides = panel.style_overrides()
    assert overrides is not None
    assert overrides.member_text.color == "#45dd7f"
    assert overrides.emoji.size_ratio == 0.8

    panel.close()


def test_main_window_has_style_panel(qapp):
    apply_theme(qapp)
    window = DanmakuStudioWindow()

    assert window.style_panel is not None
    assert window.style_panel.style_overrides() is None  # 默认未勾选
    assert window.btn_start is not None

    window.close()


def test_style_panel_rows_do_not_overlap(qapp):
    """回归：窗口高度不足时样式面板不得被压缩导致行重叠。

    曾出现 QSS min-height 不参与布局计算、以及窗口总高度不足时
    样式卡片被挤压（面板分配高度仅 140px、tabs 仅 50px）导致各行重叠。
    修复为：控件程序化最小高度 + 设置卡片放入 QScrollArea。
    本测试在真实主窗口默认尺寸下测量行几何。
    """
    apply_theme(qapp)
    window = DanmakuStudioWindow()
    window.style_panel.custom_check.setChecked(True)
    window.nav_list.setCurrentRow(1)  # 切到弹幕样式页，隐藏页面的几何坐标不可靠
    window.show()
    qapp.processEvents()

    tc = window.style_panel.text_controls
    rows = [
        ("颜色", tc.opacity_spin),
        ("描边", tc.outline_width_spin),
        ("阴影", tc.shadow_opacity_spin),
    ]
    for (name1, w1), (name2, w2) in pairwise(rows):
        bottom1 = w1.mapTo(window, w1.rect().topLeft()).y() + w1.height()
        top2 = w2.mapTo(window, w2.rect().topLeft()).y()
        assert bottom1 <= top2, f"样式行重叠: {name1}(bottom={bottom1}) > {name2}(top={top2})"

    # 颜色按钮与不透明度输入框同处一行
    color_top = tc.color_btn.mapTo(window, tc.color_btn.rect().topLeft()).y()
    spin_top = tc.opacity_spin.mapTo(window, tc.opacity_spin.rect().topLeft()).y()
    assert abs(color_top - spin_top) <= 12, "颜色按钮与不透明度输入框不在同一行"

    panel = window.style_panel
    common_bottom = (
        panel.font_combo.mapTo(window, panel.font_combo.rect().topLeft()).y()
        + panel.font_combo.height()
    )
    size_top = panel.font_size_spin.mapTo(window, panel.font_size_spin.rect().topLeft()).y()
    assert common_bottom <= size_top, "公共行重叠: 字体类型 与 字体大小"

    window.close()


# =============================================================================
# 功能同步：删除开头弹幕 / 测试片段起点 / 预览图 / 下载格式
# =============================================================================


def test_main_window_drop_first_and_clip_start_defaults(qapp):
    """删除开头弹幕默认 0；自定义起点仅在测试片段开启后可用。"""
    apply_theme(qapp)
    window = DanmakuStudioWindow()

    assert window.drop_first_spin.value() == 0
    assert window.drop_first_spin.minimum() == 0
    assert window.drop_first_spin.maximum() == 100000
    assert window.btn_drop_reference.isEnabled()

    assert not window.test_clip_check.isChecked()
    assert not window.clip_start_check.isEnabled()
    assert not window.clip_start_spin.isEnabled()

    window.test_clip_check.setChecked(True)
    assert window.clip_start_check.isEnabled()
    assert not window.clip_start_spin.isEnabled()

    window.clip_start_check.setChecked(True)
    assert window.clip_start_spin.isEnabled()

    window.test_clip_check.setChecked(False)
    assert not window.clip_start_check.isEnabled()
    assert not window.clip_start_spin.isEnabled()

    window.close()


def test_main_window_effective_config_applies_overrides(qapp, tmp_path):
    """_effective_config 应合并配置文件、样式覆盖与首条弹幕时间。"""
    apply_theme(qapp)
    window = DanmakuStudioWindow()

    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text("style:\n  font_size: 33\n", encoding="utf-8")
    window.config_edit.setText(str(cfg_file))
    window.style_panel.custom_check.setChecked(True)
    window.style_panel.font_size_spin.setValue(44)
    window.first_danmaku_check.setChecked(True)
    window.first_danmaku_spin.setValue(3.5)

    config = window._effective_config()
    assert config.style.font_size == 44
    assert config.animation.first_danmaku_at == 3.5

    window.first_danmaku_check.setChecked(False)
    config = window._effective_config()
    assert config.animation.first_danmaku_at is None  # tmp 配置未设置 → 内置默认

    window.close()


def _write_numbered_xml(tmp_path, count=5):
    xml = tmp_path / "chat.xml"
    xml.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<i>\n'
        + "".join(
            f'  <d p="{i},1,25,16777215,0,0,0,0" user="u{i}">弹幕{i}</d>\n'
            for i in range(1, count + 1)
        )
        + "</i>\n",
        encoding="utf-8",
    )
    return xml


def test_trim_reference_dialog_returns_keep_index(qapp, tmp_path):
    from danmakustudio.input.parser import parse_subtitle

    events = parse_subtitle(str(_write_numbered_xml(tmp_path)))

    apply_theme(qapp)
    dialog = TrimReferenceDialog(events)

    assert dialog.table.rowCount() == 5
    dialog.table.selectRow(2)
    assert dialog.keep_index == 3
    assert "将删除开头 2 条" in dialog.status_label.text()
    assert "保留第 3 条" in dialog.status_label.text()

    dialog.close()


def test_trim_reference_dialog_caps_at_window(qapp, tmp_path):
    from danmakustudio.input.parser import parse_subtitle
    from danmakustudio.ui.main_window import _TRIM_REF_WINDOW

    events = parse_subtitle(str(_write_numbered_xml(tmp_path, count=_TRIM_REF_WINDOW + 10)))

    apply_theme(qapp)
    dialog = TrimReferenceDialog(events)

    assert dialog.table.rowCount() == _TRIM_REF_WINDOW
    dialog.close()


def test_main_window_drop_reference_adopts_selection(qapp, tmp_path, monkeypatch):
    """参照选择确认后应把 drop_first 设置为「所选序号 - 1」。"""
    from PySide6.QtWidgets import QDialog

    from danmakustudio.batch import BatchJob

    subtitle = _write_numbered_xml(tmp_path)
    apply_theme(qapp)
    window = DanmakuStudioWindow()
    window.pending_jobs = [
        BatchJob(
            video_path=tmp_path / "v.mp4",
            subtitle_path=subtitle,
            output_path=tmp_path / "out.mp4",
        )
    ]

    class FakeDialog:
        def __init__(self, events, parent=None):
            self.events = events

        def exec(self):
            return QDialog.DialogCode.Accepted

        @property
        def keep_index(self):
            return 4

    monkeypatch.setattr(
        "danmakustudio.ui.main_window.TrimReferenceDialog", FakeDialog
    )

    window.choose_drop_first_reference()

    assert window.drop_first_spin.value() == 3
    window.close()


def test_batch_worker_accepts_drop_first_and_clip_start(qapp):
    from danmakustudio.ui.worker import BatchWorker

    worker = BatchWorker(
        jobs=[],
        encode_mode=EncodeMode.AUTO,
        config_path=None,
        force=False,
        drop_first=7,
        clip_start=12.5,
        test_clip=True,
        clip_duration=30,
    )
    assert worker.drop_first == 7
    assert worker.clip_start == 12.5


def test_download_page_format_field(qapp):
    """下载页格式字段应去除首尾空白；空值表示使用默认格式。"""
    apply_theme(qapp)
    window = DanmakuStudioWindow()

    assert window._download_format() == ""
    window.dl_format_edit.setText("  bv*[height<=720]+ba/b  ")
    assert window._download_format() == "bv*[height<=720]+ba/b"

    window.close()


def test_main_window_sidebar_navigation(qapp):
    """侧边栏应包含 4 个功能页，切换导航项应切换页面。"""
    from PySide6.QtWidgets import QPushButton

    apply_theme(qapp)
    window = DanmakuStudioWindow()

    assert window.nav_list.count() == 4
    assert window.nav_list.item(0).text() == "压制设置"
    assert window.nav_list.item(1).text() == "弹幕样式"
    assert window.nav_list.item(2).text() == "JSON 转 XML"
    assert window.nav_list.item(3).text() == "下载内容"
    assert window.pages.count() == 4
    assert window.pages.currentIndex() == 0

    window.nav_list.setCurrentRow(2)
    assert window.pages.currentIndex() == 2
    # JSON 页控件已就位
    assert any(
        btn.text() == "转换为 XML"
        for btn in window.pages.widget(2).findChildren(QPushButton)
    )

    window.close()


def test_main_window_sidebar_collapse(qapp):
    """收起侧边栏后仅保留窄条，再点恢复导航列表。"""
    apply_theme(qapp)
    window = DanmakuStudioWindow()
    window.show()
    qapp.processEvents()

    assert not window.sidebar_collapsed
    assert window.nav_list.isVisibleTo(window.sidebar)

    window.btn_toggle_sidebar.click()
    assert window.sidebar_collapsed
    assert not window.nav_list.isVisibleTo(window.sidebar)
    assert window.sidebar.width() <= 48
    assert window.btn_toggle_sidebar.text() == "»"

    window.btn_toggle_sidebar.click()
    assert not window.sidebar_collapsed
    assert window.nav_list.isVisibleTo(window.sidebar)
    assert window.btn_toggle_sidebar.text() == "«"

    window.close()


def test_main_window_task_area_below_pages(qapp):
    """任务队列应位于垂直分割器下半部分，与功能页上下分区。"""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QSplitter

    apply_theme(qapp)
    window = DanmakuStudioWindow()
    window.show()
    qapp.processEvents()

    main_split = window.findChild(QSplitter, "mainSplit")
    assert main_split is not None
    assert main_split.orientation() == Qt.Orientation.Vertical
    top_split = window.findChild(QSplitter, "topSplit")
    assert top_split is not None
    assert top_split.orientation() == Qt.Orientation.Horizontal

    # 任务卡（含日志）在页面区下方
    task_bottom = window.progress_bar.mapTo(window, window.progress_bar.rect().topLeft()).y()
    page_bottom = top_split.mapTo(window, top_split.rect().topLeft()).y() + top_split.height()
    assert task_bottom >= page_bottom - 8

    window.close()


def test_style_preview_fixed_beside_tabs(qapp):
    """样式预览应与控件区分栏同排固定，调整 tab 时无需滚动即可见。"""
    apply_theme(qapp)
    panel = StylePanel()
    panel.resize(900, 620)
    panel.show()
    qapp.processEvents()

    preview_left = panel.preview.mapTo(panel, panel.preview.rect().topLeft()).x()
    tabs_right = (
        panel.tabs.mapTo(panel, panel.tabs.rect().topLeft()).x() + panel.tabs.width()
    )
    assert preview_left >= tabs_right - 2, "预览板应在控件区右侧而非下方"

    # 预览高度不再固定为小横条，可随面板高度扩展
    assert panel.preview.height() >= 170

    panel.close()


def test_json_download_pages_hug_content(qapp):
    """JSON 与下载页的卡片应贴内容高度，不占满整个页面。"""
    from PySide6.QtWidgets import QFrame

    apply_theme(qapp)
    window = DanmakuStudioWindow()
    window.resize(1120, 780)
    window.show()
    qapp.processEvents()

    window.nav_list.setCurrentRow(2)
    qapp.processEvents()
    page = window.pages.widget(2)
    card = page.findChild(QFrame)
    assert card is not None
    assert card.height() < page.height(), "JSON 卡片不应拉伸占满页面"

    window.nav_list.setCurrentRow(3)
    qapp.processEvents()
    page3 = window.pages.widget(3)
    card3 = page3.findChild(QFrame)
    assert card3 is not None
    assert card3.height() < page3.height(), "下载卡片不应拉伸占满页面"

    window.close()
