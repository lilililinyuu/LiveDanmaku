"""弹幕文件开头删除（导出文件）单元测试。"""

import json

import pytest
from lxml import etree

from livedanmaku.input.trim import (
    default_trimmed_path,
    safe_xml_parser,
    trim_chat_json_file,
    trim_subtitle_xml_file,
)


def _chat_line(usec, text):
    return json.dumps({
        "replayChatItemAction": {"actions": [{"addChatItemAction": {
            "item": {"liveChatTextMessageRenderer": {
                "timestampUsec": str(usec),
                "authorName": {"simpleText": "u"},
                "message": {"runs": [{"text": text}]},
            }},
        }}]},
    }, ensure_ascii=False)


def _write_chat(tmp_path, messages):
    src = tmp_path / "chat.json"
    src.write_text(
        "\n".join(_chat_line(1000 + i * 1_000_000, t) for i, t in enumerate(messages)) + "\n",
        encoding="utf-8",
    )
    return src


def test_default_trimmed_path():
    src = __import__("pathlib").Path("a/直播.live_chat.json")
    assert str(default_trimmed_path(src, 5)).endswith("直播-删开头5.json")


def test_trim_chat_json_file(tmp_path):
    """删除前 2 条消息，保留行按原样写出。"""
    src = _write_chat(tmp_path, ["测试1", "测试2", "测试3", "正式"])
    dst = tmp_path / "out.json"

    removed, kept = trim_chat_json_file(src, dst, 2)

    assert (removed, kept) == (2, 2)
    lines = [json.loads(l) for l in dst.read_text(encoding="utf-8").splitlines()]
    texts = [
        l["replayChatItemAction"]["actions"][0]["addChatItemAction"]["item"]
        ["liveChatTextMessageRenderer"]["message"]["runs"][0]["text"]
        for l in lines
    ]
    assert texts == ["测试3", "正式"]
    # 源文件不受影响
    assert "测试1" in src.read_text(encoding="utf-8")


def test_trim_chat_json_file_overwrite_safe_on_error(tmp_path):
    """删除条数超限时应报错且不破坏原文件（覆盖模式安全性）。"""
    src = _write_chat(tmp_path, ["a", "b"])
    before = src.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="不少于"):
        trim_chat_json_file(src, src, 5)

    assert src.read_text(encoding="utf-8") == before


def test_trim_subtitle_xml_file(tmp_path):
    """删除前 1 条 <d>；<e> 表情元数据保持不变。"""
    src = tmp_path / "danmaku.xml"
    src.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<i>\n'
        '  <e name="smile" url="http://example.com/f.png"/>\n'
        '  <d p="1,1,25,16777215,0,0,0,0" user="u1">测试1</d>\n'
        '  <d p="2,1,25,16777215,0,0,0,0" user="u2">测试2</d>\n'
        '  <d p="3,1,25,16777215,0,0,0,0" member="1" user="u3">正式</d>\n'
        "</i>\n",
        encoding="utf-8",
    )
    dst = tmp_path / "out.xml"

    removed, kept = trim_subtitle_xml_file(src, dst, 2)

    assert (removed, kept) == (2, 1)

    root = etree.parse(str(dst), safe_xml_parser()).getroot()
    emojis = root.findall("e")
    ds = root.findall("d")
    assert len(emojis) == 1 and emojis[0].get("name") == "smile"
    assert len(ds) == 1 and ds[0].text == "正式"


def test_trim_subtitle_xml_file_overwrite(tmp_path):
    """覆盖原文件模式：原地写回删除结果。"""
    src = tmp_path / "danmaku.xml"
    src.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<i>\n'
        '  <d p="1,1,25,16777215,0,0,0,0" user="u1">a</d>\n'
        '  <d p="2,1,25,16777215,0,0,0,0" user="u2">b</d>\n'
        "</i>\n",
        encoding="utf-8",
    )

    trim_subtitle_xml_file(src, src, 1)

    ds = etree.parse(str(src), safe_xml_parser()).getroot().findall("d")
    assert len(ds) == 1 and ds[0].text == "b"


def test_trim_invalid_count(tmp_path):
    src = _write_chat(tmp_path, ["a"])
    with pytest.raises(ValueError, match="负数"):
        trim_chat_json_file(src, tmp_path / "o.json", -1)
    xml = tmp_path / "d.xml"
    xml.write_text("<i><d p=\"1\">a</d></i>", encoding="utf-8")
    with pytest.raises(ValueError, match="不少于"):
        trim_subtitle_xml_file(xml, tmp_path / "o.xml", 1)


def test_trim_chat_json_file_overwrite(tmp_path):
    """回归：覆盖原文件模式应写回删除后的完整内容（不得清空文件）。"""
    src = _write_chat(tmp_path, ["测试1", "测试2", "测试3", "正式"])

    removed, kept = trim_chat_json_file(src, src, 2)

    assert (removed, kept) == (2, 2)
    lines = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines()]
    texts = [
        l["replayChatItemAction"]["actions"][0]["addChatItemAction"]["item"]
        ["liveChatTextMessageRenderer"]["message"]["runs"][0]["text"]
        for l in lines
    ]
    assert texts == ["测试3", "正式"]
    assert not list(tmp_path.glob("*.tmp"))
