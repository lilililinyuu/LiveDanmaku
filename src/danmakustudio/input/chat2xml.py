"""YouTube 直播回放 JSON → 弹幕 XML 转换。

输入为 yt-dlp 导出的 live_chat JSON（JSONL，每条一个 replayChatItemAction），
输出为 DanmakuStudio 可用的弹幕 XML（含 <e name url> 表情元数据）。
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from xml.sax.saxutils import escape

from .event import DanmakuEvent

_EMOJI_PATTERN = re.compile(r":([^:\s]+):")


def default_xml_path_for(json_path: str | Path) -> Path:
    """聊天 JSON 对应的默认弹幕 XML 路径。

    yt-dlp 导出的弹幕文件名为 `xxx.live_chat.json`，生成的 XML 去掉
    `.live_chat` 中缀（即 `xxx.xml`），保证能被同名视频的字幕匹配规则找到。
    """
    path = Path(json_path)
    if path.stem.endswith(".live_chat"):
        return path.with_suffix("").with_suffix(".xml")
    return path.with_suffix(".xml")


def preview_json_events(json_path: str | Path, limit: int | None = None) -> list[DanmakuEvent]:
    """提取聊天 JSON 的消息，供「删除开头弹幕」参照选择展示。

    返回按时间排序的事件列表（时间为相对最早一条消息的秒数）；
    limit 不为 None 时只返回前 limit 条。
    """
    try:
        events, _ = chat_json_events(json_path)
    except ValueError:
        return []
    return events if limit is None else events[:limit]


def parse_objects_from_file(filepath):
    """按行解析 JSONL（容错：对象跨行时缓冲累积）。"""
    buffer = ""
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            buffer += line
            try:
                obj = json.loads(buffer)
                yield obj
                buffer = ""
            except json.JSONDecodeError:
                continue
        if buffer:
            try:
                obj = json.loads(buffer)
                yield obj
            except json.JSONDecodeError:
                pass


def extract_message_runs(runs):
    """从 message.runs 提取文本与表情（名字+图片 URL）列表。"""
    full_text = ""
    custom_emojis = []
    for part in runs:
        if "text" in part:
            full_text += part["text"]
            # 从纯文本中提取 :xxx: 格式的表情（备用）
            for match in _EMOJI_PATTERN.finditer(part["text"]):
                custom_emojis.append({
                    "name": match.group(0),
                    "url": "",
                    "alt": match.group(0),
                })
        elif "emoji" in part:
            emoji = part["emoji"]
            if emoji.get("isCustomEmoji", False):
                shortcuts = emoji.get("shortcuts", [])
                name = shortcuts[0] if shortcuts else ""
                thumbnails = emoji.get("image", {}).get("thumbnails", [])
                url = thumbnails[0]["url"] if thumbnails else ""
                alt = emoji.get("accessibility", {}).get("accessibilityData", {}).get("label", "")
                if name:
                    custom_emojis.append({
                        "name": name,
                        "url": url,
                        "alt": alt,
                    })
                    full_text += name
            else:
                # YouTube 平台自带表情：记录名字与图片 URL，供压制时按需下载
                shortcuts = emoji.get("shortcuts", [])
                name = shortcuts[0] if shortcuts else ""
                thumbnails = emoji.get("image", {}).get("thumbnails", [])
                url = thumbnails[0]["url"] if thumbnails else ""
                if name:
                    custom_emojis.append({
                        "name": name,
                        "url": url,
                        "alt": "",
                    })
                if shortcuts:
                    full_text += shortcuts[0]
                else:
                    alt = emoji.get("accessibility", {}).get("accessibilityData", {}).get("label", "")
                    if alt:
                        full_text += alt
    return full_text, custom_emojis


def is_membership_from_badges(badges):
    for badge in badges:
        if "liveChatAuthorBadgeRenderer" in badge:
            renderer = badge["liveChatAuthorBadgeRenderer"]
            if "customThumbnail" in renderer and "Member" in renderer.get("tooltip", ""):
                return True
    return False


def _extract_chat_entries(json_path: str | Path) -> list[dict]:
    """提取聊天 JSON 中全部普通文本消息，返回按时间排序的条目列表。

    条目字段：ts（Unix 秒）、author、text（表情已转 [name] 形式）、
    emojis（{name,url} 列表）、is_member。
    """
    entries: list[dict] = []
    for obj in parse_objects_from_file(str(json_path)):
        replay_action = obj.get("replayChatItemAction")
        if not replay_action:
            continue
        for action in replay_action.get("actions", []):
            add_item = action.get("addChatItemAction")
            if not add_item:
                continue
            item = add_item.get("item")
            if not item:
                continue
            renderer = item.get("liveChatTextMessageRenderer")
            if not renderer:
                continue

            timestamp_usec = renderer.get("timestampUsec")
            if not timestamp_usec:
                continue
            try:
                ts = int(timestamp_usec) / 1_000_000
            except (TypeError, ValueError):
                continue

            author_name = renderer.get("authorName", {}).get("simpleText", "")
            is_member = is_membership_from_badges(renderer.get("authorBadges", []))
            message_runs = renderer.get("message", {}).get("runs", [])
            text, emojis = extract_message_runs(message_runs)
            if not text and not emojis:
                continue
            text = _EMOJI_PATTERN.sub(lambda m: f"[{m.group(1)}]", text).strip()

            entries.append({
                "ts": ts,
                "author": author_name,
                "text": text,
                "emojis": emojis,
                "is_member": is_member,
            })

    entries.sort(key=lambda e: e["ts"])
    return entries


def chat_json_events(json_path: str | Path) -> tuple[list[DanmakuEvent], dict[str, str]]:
    """把聊天 JSON 解析为弹幕事件列表与表情 URL 表。

    时间为相对最早一条消息的秒数（与 XML 时间轴一致）。供压制、预览、
    删除开头弹幕参照选择直接消费 JSON，无需先转 XML。
    """
    entries = _extract_chat_entries(json_path)
    if not entries:
        raise ValueError("未提取到任何弹幕消息")

    base = entries[0]["ts"]
    events = [
        DanmakuEvent(
            time=e["ts"] - base,
            user=e["author"] or "匿名",
            text=e["text"],
            is_membership=e["is_member"],
        )
        for e in entries
        if e["text"]
    ]

    emoji_map: dict[str, str] = {}
    for e in entries:
        for emo in e["emojis"]:
            name = emo.get("name", "").strip(":")
            url = emo.get("url", "")
            if name and url and name not in emoji_map:
                emoji_map[name] = url
    return events, emoji_map


def convert_chat_json(
    json_path: str | Path,
    xml_path: str | Path,
    lead_in: float = 0.0,
    drop_first: int = 0,
) -> dict:
    """把 YouTube 直播回放 JSON 转为弹幕 XML。

    Args:
        json_path: 输入 JSON 文件（JSONL 格式的 live_chat）
        xml_path: 输出 XML 文件
        lead_in: 第一条弹幕相对视频开始的时间（秒）
        drop_first: 转换前删除开头的消息条数（如直播开始前的测试弹幕）

    Returns:
        统计信息：{"messages": 条数, "dropped": 删除条数,
        "start_time": 视频起始时间 ISO, "lead_in": 秒}
    """
    if drop_first < 0:
        raise ValueError(f"删除条数不能为负数，当前 {drop_first}")
    data_entries = _extract_chat_entries(json_path)
    if not data_entries:
        raise ValueError("未提取到任何弹幕消息")

    if drop_first:
        if drop_first >= len(data_entries):
            raise ValueError(
                f"删除条数 {drop_first} 不少于消息总数 {len(data_entries)}"
            )
        del data_entries[:drop_first]

    base_ts = data_entries[0]["ts"]
    start_dt = datetime.fromtimestamp(base_ts - lead_in, tz=UTC)

    # 汇总唯一表情的名字与图片 URL，写入 <e> 标签供压制时按需下载
    emoji_map: dict[str, str] = {}
    for item in data_entries:
        for emo in item["emojis"]:
            name = emo.get("name", "").strip(":")
            url = emo.get("url", "")
            if name and url and name not in emoji_map:
                emoji_map[name] = url

    xml_lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<i>"]
    for name, url in sorted(emoji_map.items()):
        xml_lines.append(f'  <e name="{escape(name)}" url="{escape(url)}"/>')

    for item in data_entries:
        offset = item["ts"] - base_ts + lead_in
        if offset < 0:
            continue
        text = item["text"]
        if not text:
            continue
        member = "1" if item["is_member"] else "0"
        author = item["author"] or "匿名"
        xml_lines.append(
            f'  <d p="{offset:.1f},1,25,16777215,0,0,0,0" member="{member}" '
            f'user="{escape(author)}">{escape(text)}</d>'
        )

    xml_lines.append("</i>")
    with open(xml_path, "w", encoding="utf-8") as f:
        f.write("\n".join(xml_lines))

    return {
        "messages": len(data_entries),
        "dropped": drop_first,
        "start_time": start_dt.isoformat(),
        "lead_in": lead_in,
    }
