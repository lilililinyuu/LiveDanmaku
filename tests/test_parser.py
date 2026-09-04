"""parser.py 单元测试

测试 XML 弹幕解析的各种场景，包括正常解析、异常分支和边界条件。
"""

import sys
from pathlib import Path

import pytest
from PySide6.QtGui import QGuiApplication

from danmakustudio.input.event import DanmakuEvent
from danmakustudio.input.parser import (
    align_first_event_time,
    parse_lrc,
    parse_subtitle,
    parse_subtitle_with_emoji,
    parse_xml,
)


@pytest.fixture(scope="session")
def qapp():
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    yield app


def _write_xml(tmp_path: Path, content: str) -> str:
    """创建临时 XML 文件并返回路径。"""
    xml_file = tmp_path / "test.xml"
    xml_file.write_text(content, encoding="utf-8")
    return str(xml_file)


def _write_lrc(tmp_path: Path, content: str) -> str:
    """创建临时 LRC 文件并返回路径。"""
    lrc_file = tmp_path / "test.lrc"
    lrc_file.write_text(content, encoding="utf-8")
    return str(lrc_file)


# =============================================================================
# 正常解析
# =============================================================================


class TestParseDanmaku:
    """测试 <d> 标签（普通弹幕）解析"""

    def test_single_danmaku(self, tmp_path):
        """单条弹幕应正确解析时间、用户和文本。"""
        xml = '<i><d p="1.5,1,25,16777215,1234567890,0,user1,0" user="user1">你好</d></i>'
        path = _write_xml(tmp_path, xml)
        events = parse_xml(path)
        assert len(events) == 1
        assert events[0].time == 1.5
        assert events[0].user == "user1"
        assert events[0].text == "你好"

    def test_danmaku_with_uid_attribute(self, tmp_path):
        """uid 属性应作为用户名回退。"""
        xml = '<i><d p="2.0,1,25,16777215,0,0,0,0" uid="user2">世界</d></i>'
        path = _write_xml(tmp_path, xml)
        events = parse_xml(path)
        assert len(events) == 1
        assert events[0].user == "user2"

    def test_danmaku_no_user_defaults_anonymous(self, tmp_path):
        """无用户属性时默认为"匿名"。"""
        xml = '<i><d p="3.0,1,25,16777215,0,0,0,0">无用户</d></i>'
        path = _write_xml(tmp_path, xml)
        events = parse_xml(path)
        assert events[0].user == "匿名"

    def test_danmaku_empty_text(self, tmp_path):
        """空文本应保留为空字符串。"""
        xml = '<i><d p="4.0,1,25,16777215,0,0,0,0"></d></i>'
        path = _write_xml(tmp_path, xml)
        events = parse_xml(path)
        assert len(events) == 1
        assert events[0].text == ""

    def test_multiple_danmakus_sorted_by_time(self, tmp_path):
        """多条弹幕应按时间升序排列。"""
        xml = """<i>
            <d p="5.0,1,25,16777215,0,0,a,0">第五秒</d>
            <d p="1.0,1,25,16777215,0,0,b,0">第一秒</d>
            <d p="3.0,1,25,16777215,0,0,c,0">第三秒</d>
        </i>"""
        path = _write_xml(tmp_path, xml)
        events = parse_xml(path)
        assert len(events) == 3
        assert [e.time for e in events] == [1.0, 3.0, 5.0]


# =============================================================================
# 异常分支
# =============================================================================


class TestParseMalformed:
    """测试畸形 XML 的容错处理"""

    def test_danmaku_missing_p_attr(self, tmp_path):
        """缺少 p 属性的 <d> 标签应被跳过。"""
        xml = '<i><d>没有 p 属性</d></i>'
        path = _write_xml(tmp_path, xml)
        events = parse_xml(path)
        assert len(events) == 0

    def test_danmaku_invalid_p_attr(self, tmp_path):
        """p 属性格式无效的 <d> 标签应被跳过。"""
        xml = '<i><d p="invalid_p_attr">无效 p</d></i>'
        path = _write_xml(tmp_path, xml)
        events = parse_xml(path)
        assert len(events) == 0

    def test_empty_xml(self, tmp_path):
        """空 XML 应返回空列表。"""
        xml = "<i></i>"
        path = _write_xml(tmp_path, xml)
        events = parse_xml(path)
        assert len(events) == 0

    def test_other_tags_ignored(self, tmp_path):
        """非 <d> 标签应被忽略。"""
        xml = '<i><sc ts="1.0" user="u">Super Chat</sc></i>'
        path = _write_xml(tmp_path, xml)
        events = parse_xml(path)
        assert len(events) == 0


# =============================================================================
# 混合场景
# =============================================================================


class TestParseMixed:
    """测试多条弹幕与其它标签混合解析"""

    def test_danmakus_sorted_with_other_tags(self, tmp_path):
        """弹幕应按时间统一排序，其它标签被忽略。"""
        xml = """<i>
            <d p="1.0,1,25,16777215,0,0,a,0">弹幕1</d>
            <sc ts="2.0" user="b">Super Chat</sc>
            <d p="3.0,1,25,16777215,0,0,c,0">弹幕2</d>
        </i>"""
        path = _write_xml(tmp_path, xml)
        events = parse_xml(path)
        assert len(events) == 2
        assert [e.text for e in events] == ["弹幕1", "弹幕2"]
        assert [e.time for e in events] == [1.0, 3.0]


# =============================================================================
# 表情元数据（<e> 标签）
# =============================================================================


def test_parse_subtitle_with_emoji_metadata(tmp_path):
    """<e name url> 标签应被收集为表情名 -> URL 映射，<d> 正常解析。"""
    xml = (
        '<i>'
        '<e name="face-blue-smiling" url="http://example.com/face.png"/>'
        '<e name="cat-orange" url="http://example.com/cat.png"/>'
        '<d p="1.0,1,25,16777215,0,0,a,0">[face-blue-smiling]</d>'
        '</i>'
    )
    path = _write_xml(tmp_path, xml)

    events, emoji_map = parse_subtitle_with_emoji(path)

    assert len(events) == 1
    assert emoji_map == {
        "face-blue-smiling": "http://example.com/face.png",
        "cat-orange": "http://example.com/cat.png",
    }
    # 兼容入口只返回事件
    assert len(parse_subtitle(path)) == 1


# =============================================================================
# 首条弹幕时间对齐
# =============================================================================


def test_align_first_event_time():
    """整体平移应把最早事件对齐到目标秒，且保持相互间隔。"""
    events = [
        DanmakuEvent(time=5.0, user="a", text="x"),
        DanmakuEvent(time=8.0, user="b", text="y"),
    ]

    align_first_event_time(events, 2.0)
    assert [e.time for e in events] == [2.0, 5.0]

    align_first_event_time(events, 10.0)  # 推迟
    assert [e.time for e in events] == [10.0, 13.0]

    align_first_event_time(events, 0.0)  # 视频一开始就出现
    assert [e.time for e in events] == [0.0, 3.0]

    align_first_event_time([], 2.0)  # 空列表不报错


# =============================================================================
# LRC 解析
# =============================================================================


class TestParseLrc:
    """测试 LRC 字幕解析"""

    def test_single_lrc_line(self, tmp_path):
        """单行 LRC 应正确解析时间和文本。"""
        path = _write_lrc(tmp_path, "[00:01.50]第一句")
        events = parse_lrc(path)
        assert len(events) == 1
        assert events[0].time == 1.5
        assert events[0].user == ""
        assert events[0].text == "第一句"

    def test_lrc_colon_username(self, tmp_path):
        """冒号分隔的 LRC 弹幕应识别用户名。"""
        path = _write_lrc(tmp_path, "[00:01.00]广州本地小孩: 队长是体验卡吗")
        events = parse_lrc(path)
        assert events[0].user == "广州本地小孩"
        assert events[0].text == "队长是体验卡吗"

    def test_lrc_fullwidth_colon_username(self, tmp_path):
        """全角冒号分隔的 LRC 弹幕应识别用户名。"""
        path = _write_lrc(tmp_path, "[00:01.00]沐源安：就怕公司不让她回去")
        events = parse_lrc(path)
        assert events[0].user == "沐源安"
        assert events[0].text == "就怕公司不让她回去"

    def test_lrc_tab_username(self, tmp_path):
        """Tab 分隔的 LRC 弹幕应识别用户名。"""
        path = _write_lrc(tmp_path, "[00:01.00]QQYYQQ\t感觉很舒服")
        events = parse_lrc(path)
        assert events[0].user == "QQYYQQ"
        assert events[0].text == "感觉很舒服"

    def test_lrc_wide_space_username(self, tmp_path):
        """多空格分隔的 LRC 弹幕应识别用户名。"""
        path = _write_lrc(tmp_path, "[00:01.00]莫mo    舒服了")
        events = parse_lrc(path)
        assert events[0].user == "莫mo"
        assert events[0].text == "舒服了"

    def test_lrc_single_space_likely_username(self, tmp_path):
        """明显用户名的单空格写法也应兼容。"""
        path = _write_lrc(tmp_path, "[00:01.00]QQYYQQ 感觉很舒服")
        events = parse_lrc(path)
        assert events[0].user == "QQYYQQ"
        assert events[0].text == "感觉很舒服"

    def test_lrc_single_space_cjk_username(self, tmp_path):
        """中文用户名的单空格写法也应兼容弹幕导出文件。"""
        path = _write_lrc(tmp_path, "[00:01.00]地平线被淘汰之王 晚上好")
        events = parse_lrc(path)
        assert events[0].user == "地平线被淘汰之王"
        assert events[0].text == "晚上好"

    def test_lrc_plain_lyrics_not_split_as_username(self, tmp_path):
        """普通歌词行不应被误拆成用户名。"""
        path = _write_lrc(tmp_path, "[00:01.00]Hello world")
        events = parse_lrc(path)
        assert events[0].user == ""
        assert events[0].text == "Hello world"

    def test_hour_timestamp(self, tmp_path):
        """小时格式时间戳应正确解析。"""
        path = _write_lrc(tmp_path, "[01:02:03.456]长视频字幕")
        events = parse_lrc(path)
        assert events[0].time == pytest.approx(3723.456)

    def test_multiple_timestamps_one_line(self, tmp_path):
        """同一行多个时间戳应生成多条事件。"""
        path = _write_lrc(tmp_path, "[00:02.00][00:01.00]重复句")
        events = parse_lrc(path)
        assert [e.time for e in events] == [1.0, 2.0]
        assert [e.text for e in events] == ["重复句", "重复句"]

    def test_metadata_ignored(self, tmp_path):
        """LRC 元数据行应被忽略。"""
        path = _write_lrc(tmp_path, "[ar:作者]\n[00:01.00]正文")
        events = parse_lrc(path)
        assert len(events) == 1
        assert events[0].text == "正文"

    def test_parse_subtitle_lrc_delegates(self, tmp_path):
        """自动解析入口应支持 LRC。"""
        path = _write_lrc(tmp_path, "[00:01.00]正文")
        events = parse_subtitle(path)
        assert len(events) == 1
        assert events[0].text == "正文"
