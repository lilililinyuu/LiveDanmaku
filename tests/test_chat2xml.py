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
