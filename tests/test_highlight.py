"""直播高潮点分析单元测试。"""

import json

from livedanmaku.core.highlight import (
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


def test_analyze_highlights_uniform_distribution_has_no_highlight():
    """弹幕分布均匀、没有相对高峰时应返回空（高潮点定义要求相对更密集）。"""
    times = [float(i) * 1.0 for i in range(100)]         # 每 10 秒恰好 1 条

    assert analyze_highlights(times, bucket=10.0, top=3) == []


def test_analyze_highlights_caps_segment_at_max_duration():
    """连续 10 分钟的高发段应被截断为最长 5 分钟。"""
    times = [float(i * 30) for i in range(30)]           # 背景稀疏
    times += [1000.0 + i * 1.0 for i in range(600)]      # 1000~1600s，每秒 1 条

    intervals = analyze_highlights(times, bucket=10.0, top=5)

    assert len(intervals) == 1
    it = intervals[0]
    assert it.end - it.start <= 300.0
    assert 1000.0 <= it.start < 1300.0   # 均匀分布时保留最靠前的最优窗口
    assert it.count == 300


def test_analyze_highlights_top_limit():
    """top 参数限制返回段数：两个高潮只取弹幕最多的前 1 段。"""
    times = [float(i * 30) for i in range(30)]
    times += [1000.0 + i * 0.5 for i in range(40)]       # 合计 40 条
    times += [2000.0 + i * 0.4 for i in range(50)]       # 合计 50 条

    intervals = analyze_highlights(times, bucket=10.0, top=1)

    assert len(intervals) == 1
    assert intervals[0].count == 50
    assert intervals[0].start <= 2000.0 <= intervals[0].end


def test_analyze_highlights_invalid_input():
    assert analyze_highlights([], bucket=10.0, top=5) == []
    assert analyze_highlights([1.0, 2.0], bucket=0, top=5) == []
    assert analyze_highlights([1.0, 2.0], bucket=10.0, top=0) == []


def test_format_clock():
    assert format_clock(0) == "00:00"
    assert format_clock(65.9) == "01:05"
    assert format_clock(3600 + 125) == "1:02:05"
