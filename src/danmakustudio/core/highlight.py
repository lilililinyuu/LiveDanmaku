"""直播高潮点分析。

根据 yt-dlp 导出的直播聊天 JSON 中的消息时间戳，统计弹幕发送频率，
找出最热闹的时间段（高潮点），供快速定位直播精彩部分。

时间均以最早一条弹幕为 0 秒的相对秒数表示（与弹幕 XML 的时间轴一致，
未设置 lead_in 时即视频时间）。
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from ..input.chat2xml import parse_objects_from_file


@dataclass(frozen=True, slots=True)
class HighlightInterval:
    """一个高潮时间段。"""

    start: float   # 起始秒（相对首条弹幕）
    end: float     # 结束秒
    count: int     # 区间内弹幕总数
    peak: int      # 单个统计窗口内的最大条数


def extract_chat_times(json_path: str) -> list[float]:
    """提取聊天 JSON 中全部消息的时间，返回相对最早消息的秒数列表（升序）。

    与 json2xml 不同，这里统计所有类型的聊天消息（普通弹幕、Super Chat、
    贴纸等），它们都反映直播间的活跃程度。
    """
    times: list[float] = []
    for obj in parse_objects_from_file(str(json_path)):
        replay_action = obj.get("replayChatItemAction")
        if not replay_action:
            continue
        for action in replay_action.get("actions", []):
            item = action.get("addChatItemAction", {}).get("item")
            if not item:
                continue
            for renderer in item.values():
                if not isinstance(renderer, dict):
                    continue
                usec = renderer.get("timestampUsec")
                if not isinstance(usec, str):
                    continue
                try:
                    times.append(int(usec) / 1_000_000)
                except ValueError:
                    continue
                break
    if not times:
        return []
    base = min(times)
    return sorted(t - base for t in times)


def analyze_highlights(
    times: list[float],
    bucket: float = 10.0,
    top: int = 5,
) -> list[HighlightInterval]:
    """按固定时间窗口统计弹幕数，合并相邻热点窗口，返回最热闹的前 top 段。

    热点判定：窗口条数 >= max(2, 平均值的 2 倍)；若无窗口达标（弹幕分布
    均匀），退化为直接取条数最多的前 top 个窗口。
    返回结果按时间先后排序。
    """
    if not times or bucket <= 0 or top <= 0:
        return []

    n_buckets = int(times[-1] // bucket) + 1
    counts = [0] * n_buckets
    for t in times:
        counts[int(t // bucket)] += 1

    mean = len(times) / n_buckets
    threshold = max(2, mean * 2)
    hot = [i for i, c in enumerate(counts) if c >= threshold]
    if not hot:
        ranked = sorted(range(n_buckets), key=lambda i: counts[i], reverse=True)
        hot = [i for i in ranked if counts[i] > 0][:top]

    # 合并相邻（含间隔 1 个冷窗口）的热点窗口成段
    segments: list[list[int]] = []
    for i in hot:
        if segments and i - segments[-1][-1] <= 2:
            segments[-1].append(i)
        else:
            segments.append([i])

    intervals = [
        HighlightInterval(
            start=seg[0] * bucket,
            end=(seg[-1] + 1) * bucket,
            count=sum(counts[i] for i in seg),
            peak=max(counts[i] for i in seg),
        )
        for seg in segments
    ]
    intervals.sort(key=lambda it: it.count, reverse=True)
    intervals = intervals[:top]
    intervals.sort(key=lambda it: it.start)
    logger.debug(
        "高潮点分析: {} 条弹幕 / {} 个窗口, 阈值 {:.1f}, 命中 {} 段",
        len(times), n_buckets, threshold, len(intervals),
    )
    return intervals


def format_clock(seconds: float) -> str:
    """把秒数格式化为 MM:SS 或 HH:MM:SS。"""
    total = max(0, int(seconds))
    minutes, sec = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"
