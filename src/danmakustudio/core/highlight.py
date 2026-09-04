"""直播高潮点分析。

根据 yt-dlp 导出的直播聊天 JSON 中的消息时间戳，统计弹幕发送频率，
找出最热闹的时间段（高潮点），供快速定位直播精彩部分。

时间均以最早一条弹幕为 0 秒的相对秒数表示（与弹幕 XML 的时间轴一致，
未设置 lead_in 时即视频时间）。
"""

from __future__ import annotations

import statistics
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
    max_duration: float = 300.0,
) -> list[HighlightInterval]:
    """按弹幕发送频率找出"高潮点"时间段。

    高潮点的定义（满足其一即可）：
    - **相对突增**：某时间窗口的弹幕数明显多于前后邻近区段（判据：窗口
      条数 >= max(3, 前后基线的 3 倍)，基线为前后各约 3 分钟窗口的平均值）；
    - **持续高发**：窗口条数明显高于全片中位水平（>= max(5, 中位数的 3 倍），
      用于捕捉持续数分钟的密集刷屏段——这类段内部的窗口相对邻近区段
      并不突增。

    合并相邻热点后，单段时长不超过 max_duration 秒（默认 5 分钟）——
    超长时段只保留其中最热闹的连续部分。

    返回按总条数排名的前 top 段，结果按时间先后排序；弹幕分布均匀、
    没有相对高峰时返回空列表。
    """
    if not times or bucket <= 0 or top <= 0 or max_duration <= 0:
        return []

    n_buckets = int(times[-1] // bucket) + 1
    counts = [0] * n_buckets
    for t in times:
        counts[int(t // bucket)] += 1

    global_median = statistics.median(counts)
    absolute_floor = max(5, global_median * 3)
    context = max(3, int(180 // bucket))
    hot: list[int] = []
    for i, c in enumerate(counts):
        if c >= absolute_floor:
            hot.append(i)
            continue
        lo = max(0, i - context)
        hi = min(n_buckets, i + context + 1)
        neighbours = [counts[j] for j in range(lo, hi) if j != i]
        baseline = (sum(neighbours) / len(neighbours)) if neighbours else 0.0
        if c >= max(3, baseline * 3):
            hot.append(i)

    if not hot:
        return []

    # 合并相邻热点（间隔不超过 2 个冷窗口视为同一段）
    segments: list[list[int]] = []
    for i in hot:
        if segments and i - segments[-1][-1] <= 3:
            segments[-1].append(i)
        else:
            segments.append([i])

    # 单段最长 max_duration：超长时段取其中总条数最大的连续部分
    cap_buckets = max(1, int(max_duration // bucket))
    trimmed: list[list[int]] = []
    for seg in segments:
        if len(seg) <= cap_buckets:
            trimmed.append(seg)
            continue
        best_lo, best_sum = seg[0], -1
        for lo in range(seg[0], seg[-1] - cap_buckets + 2):
            window_sum = sum(counts[j] for j in range(lo, lo + cap_buckets))
            if window_sum > best_sum:
                best_lo, best_sum = lo, window_sum
        trimmed.append(list(range(best_lo, best_lo + cap_buckets)))
    segments = trimmed

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
        "高潮点分析: {} 条弹幕 / {} 个窗口, 命中 {} 段",
        len(times), n_buckets, len(intervals),
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
