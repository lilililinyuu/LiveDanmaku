"""直播高潮点分析单元测试。"""

import json

from danmakustudio.core.highlight import (
    analyze_highlights,
    extract_chat_times,
    format_clock,
)


def _chat_line(usec: int, renderer: str = "liveChatTextMessageRenderer") -> str:
    return json.dumps({
        "replayChatItemAction": {"actions": [{
            "addChatItemAction": {"item": {renderer: {
                "timestampUsec": str(usec),
                "authorName": {"simpleText": "u"},
                "message": {"runs": [{"text": "hi"}]},
            }}},
        }]},
    }) + "\n"


def test_extract_chat_times_relative_to_first(tmp_path):
    base_us = 1_700_000_000_000_000
    lines = "".join([
        _chat_line(base_us + 5_000_000),    # +5s
        _chat_line(base_us),                # 0s（首条）
        _chat_line(base_us + 12_500_000),   # +12.5s
        '{"unknown": {}}\n',                # 非聊天对象应被跳过
    ])
    path = tmp_path / "chat.json"
    path.write_text(lines, encoding="utf-8")

    times = extract_chat_times(str(path))

    assert times == [0.0, 5.0, 12.5]


def test_extract_chat_times_counts_super_chat(tmp_path):
    """Super Chat 等其它消息类型同样计入活跃度。"""
    path = tmp_path / "chat.json"
    path.write_text(
        _chat_line(1_700_000_000_000_000, renderer="liveChatSuperChatMessageRenderer"),
        encoding="utf-8",
    )

    assert extract_chat_times(str(path)) == [0.0]


def test_extract_chat_times_empty(tmp_path):
    path = tmp_path / "chat.json"
    path.write_text('{"unknown": {}}\n', encoding="utf-8")
    assert extract_chat_times(str(path)) == []


def test_analyze_highlights_finds_dense_cluster():
    """稀疏背景 + 密集簇：高潮段应落在簇所在位置。"""
    times = [float(i * 30) for i in range(30)]           # 0~870s，每 30 秒一条
    times += [1000.0 + i * 0.5 for i in range(40)]       # 1000s 附近，20 秒内 40 条
    times += [2000.0 + i * 0.4 for i in range(50)]       # 2000s 附近，20 秒内 50 条

    intervals = analyze_highlights(times, bucket=10.0, top=2)

    assert len(intervals) == 2
    assert intervals[0].start <= 1000.0 <= intervals[0].end
    assert intervals[1].start <= 2000.0 <= intervals[1].end
    assert intervals[1].count >= intervals[0].count


def test_analyze_highlights_merges_adjacent_hot_buckets():
    """相邻的热点窗口应合并为一段。"""
    times = [float(i * 30) for i in range(30)]
    times += [1000.0 + i * 1.0 for i in range(30)]       # 1000~1030s，连续 3 个热窗口

    intervals = analyze_highlights(times, bucket=10.0, top=5)

    assert len(intervals) == 1
    assert intervals[0].start == 1000.0
    assert intervals[0].end >= 1030.0
    assert intervals[0].count == 30


def test_analyze_highlights_uniform_distribution_falls_back_to_top():
    """分布均匀无热点时，退化为取条数最多的窗口。"""
    times = [float(i) * 1.0 for i in range(100)]         # 每 10 秒恰好 1 条

    intervals = analyze_highlights(times, bucket=10.0, top=3)

    assert 0 < len(intervals) <= 3


def test_analyze_highlights_invalid_input():
    assert analyze_highlights([], bucket=10.0, top=5) == []
    assert analyze_highlights([1.0, 2.0], bucket=0, top=5) == []
    assert analyze_highlights([1.0, 2.0], bucket=10.0, top=0) == []


def test_format_clock():
    assert format_clock(0) == "00:00"
    assert format_clock(65.9) == "01:05"
    assert format_clock(3600 + 125) == "1:02:05"
