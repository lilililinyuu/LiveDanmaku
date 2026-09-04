"""chat2xml 转换模块测试。"""

import json

import pytest

from danmakustudio.input.chat2xml import convert_chat_json
from danmakustudio.input.parser import parse_subtitle_with_emoji


def _replay_line(usec, author, runs, member=False):
    """构造一条 raw live_chat replay JSONL 记录。"""
    obj = {
        "replayChatItemAction": {
            "actions": [{
                "addChatItemAction": {
                    "item": {
                        "liveChatTextMessageRenderer": {
                            "timestampUsec": str(usec),
                            "authorName": {"simpleText": author},
                            "authorBadges": (
                                [{"liveChatAuthorBadgeRenderer": {
                                    "customThumbnail": {"thumbnails": [{}]},
                                    "tooltip": "Member (1 month)",
                                }}]
                                if member else []
                            ),
                            "message": {"runs": runs},
                        }
                    }
                }
            }]
        }
    }
    return json.dumps(obj)


def _text_runs(text):
    return [{"text": text}]


def _emoji_run(shortcut, url):
    return [{"emoji": {
        "isCustomEmoji": False,
        "shortcuts": [shortcut],
        "image": {"thumbnails": [{"url": url}]},
        "accessibility": {"accessibilityData": {"label": ""}},
    }}]


def test_convert_chat_json_basic(tmp_path):
    """两条消息应正确转换，lead-in 与会员标记生效。"""
    src = tmp_path / "chat.json"
    src.write_text(
        _replay_line(1753357638681000, "user1", _text_runs("hello")) + "\n"
        + _replay_line(1753357640681000, "user2", _text_runs("world"), member=True) + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.xml"

    stats = convert_chat_json(src, out, lead_in=5.0)

    assert stats["messages"] == 2
    assert stats["lead_in"] == 5.0

    events, emoji_map = parse_subtitle_with_emoji(str(out))
    assert len(events) == 2
    assert events[0].time == pytest.approx(5.0)
    assert events[1].time == pytest.approx(7.0)  # 间隔 2 秒
    assert events[0].user == "user1"
    assert events[0].text == "hello"
    assert events[1].is_membership is True
    assert emoji_map == {}


def test_convert_chat_json_emoji_metadata(tmp_path):
    """平台表情应写入 <e> 元数据，文本转换为 [name] 形式。"""
    src = tmp_path / "chat.json"
    src.write_text(
        _replay_line(
            1753357638681000, "user1",
            _text_runs("hi ") + _emoji_run(":face-blue-smiling:", "http://example.com/face.png"),
        )
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.xml"

    convert_chat_json(src, out, lead_in=0.0)

    events, emoji_map = parse_subtitle_with_emoji(str(out))
    assert len(events) == 1
    assert "[face-blue-smiling]" in events[0].text
    assert emoji_map == {"face-blue-smiling": "http://example.com/face.png"}


def test_convert_chat_json_empty_raises(tmp_path):
    src = tmp_path / "chat.json"
    src.write_text('{"notReplay": {}}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="未提取到任何弹幕消息"):
        convert_chat_json(src, tmp_path / "out.xml")


def test_default_xml_path_for_strips_live_chat():
    """yt-dlp 的 xxx.live_chat.json 应映射为 xxx.xml；普通 json 原后缀替换。"""
    from pathlib import Path

    from danmakustudio.input.chat2xml import default_xml_path_for

    assert default_xml_path_for("a/直播.live_chat.json") == Path("a/直播.xml")
    assert default_xml_path_for("a/chat.json") == Path("a/chat.xml")


def test_convert_chat_json_drop_first(tmp_path):
    """drop_first 应在计算时间基准前删除开头消息。"""
    base = 1753357638681000
    src = tmp_path / "chat.json"
    src.write_text(
        _replay_line(base, "u1", _text_runs("测试1")) + "\n"
        + _replay_line(base + 2_000_000, "u2", _text_runs("测试2")) + "\n"
        + _replay_line(base + 5_000_000, "u3", _text_runs("正式")) + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.xml"

    stats = convert_chat_json(src, out, lead_in=0.0, drop_first=2)

    assert stats["messages"] == 1
    assert stats["dropped"] == 2

    events, _ = parse_subtitle_with_emoji(str(out))
    assert len(events) == 1
    assert events[0].time == pytest.approx(0.0)
    assert events[0].text == "正式"


def test_convert_chat_json_drop_first_too_many(tmp_path):
    """删除条数不少于消息总数时应报错。"""
    src = tmp_path / "chat.json"
    src.write_text(_replay_line(1753357638681000, "u1", _text_runs("hi")) + "\n",
                   encoding="utf-8")

    with pytest.raises(ValueError, match="不少于"):
        convert_chat_json(src, tmp_path / "out.xml", drop_first=1)


def test_preview_json_events(tmp_path):
    """preview_json_events 应返回相对时间排序的前 limit 条消息。"""
    from danmakustudio.input.chat2xml import preview_json_events

    base = 1753357638681000
    src = tmp_path / "chat.json"
    src.write_text(
        _replay_line(base + 3_000_000, "u2", _text_runs("第二")) + "\n"
        + _replay_line(base, "u1", _text_runs("第一")) + "\n"
        + _replay_line(base + 8_000_000, "u3", _text_runs("第三")) + "\n",
        encoding="utf-8",
    )

    events = preview_json_events(str(src))
    assert [(e.time, e.user, e.text) for e in events] == [
        (0.0, "u1", "第一"),
        (3.0, "u2", "第二"),
        (8.0, "u3", "第三"),
    ]

    limited = preview_json_events(str(src), limit=2)
    assert len(limited) == 2
    assert limited[0].text == "第一"
