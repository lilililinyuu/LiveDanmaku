"""YouTube 直播回放 JSON → 弹幕 XML 转换。

输入为 yt-dlp 导出的 live_chat JSON（JSONL，每条一个 replayChatItemAction），
输出为 DanmakuStudio 可用的弹幕 XML（含 <e name url> 表情元数据）。
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
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


def preview_json_events(json_path: str | Path, limit: int = 40) -> list[DanmakuEvent]:
    """提取聊天 JSON 开头的消息，供「删除开头弹幕」参照选择展示。

    返回按时间排序的前 limit 条，时间为相对最早一条消息的秒数；
    只统计普通文本消息（与 XML 转换的范围一致）。
    """
    raw: list[tuple[float, str, str]] = []
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
            usec = renderer.get("timestampUsec")
            try:
                ts = int(usec) / 1_000_000
            except (TypeError, ValueError):
                continue
            author = renderer.get("authorName", {}).get("simpleText", "") or "匿名"
            text, _ = extract_message_runs(renderer.get("message", {}).get("runs", []))
            text = text.strip()
            if not text:
                continue
            raw.append((ts, author, text))

    if not raw:
        return []
    base = min(t for t, _, _ in raw)
    raw.sort(key=lambda e: e[0])
    return [
        DanmakuEvent(time=t - base, user=author, text=text)
        for t, author, text in raw[:limit]
    ]


def parse_timestamp_usec(usec_str):
    """把 timestampUsec（微秒）转为 ISO 时间戳字符串。"""
    try:
        us = int(usec_str)
        dt = datetime.fromtimestamp(us / 1_000_000, tz=UTC)
        return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except (TypeError, ValueError):
        return None


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
    data_entries = []
    for obj in parse_objects_from_file(str(json_path)):
        replay_action = obj.get("replayChatItemAction")
        if not replay_action:
            continue
        actions = replay_action.get("actions", [])
        for action in actions:
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
            timestamp_str = parse_timestamp_usec(timestamp_usec)
            if not timestamp_str:
                continue

            author_name = renderer.get("authorName", {}).get("simpleText", "")
            badges = renderer.get("authorBadges", [])
            is_member = is_membership_from_badges(badges)

            message_runs = renderer.get("message", {}).get("runs", [])
            text, emojis = extract_message_runs(message_runs)
            if not text and not emojis:
                continue

            data_entries.append({
                "timestamp": timestamp_str,
                "author": author_name,
                "message": text,
                "customEmojis": emojis,
                "isMembership": is_member,
                "isVerified": False,
            })

    if not data_entries:
        raise ValueError("未提取到任何弹幕消息")

    data_entries.sort(key=lambda x: x["timestamp"])
    if drop_first:
        if drop_first >= len(data_entries):
            raise ValueError(
                f"删除条数 {drop_first} 不少于消息总数 {len(data_entries)}"
            )
        del data_entries[:drop_first]

    first_time = data_entries[0]["timestamp"]
    start_dt = (
        datetime.fromisoformat(first_time.replace("Z", "+00:00"))
        - timedelta(seconds=lead_in)
    )

    # 汇总唯一表情的名字与图片 URL，写入 <e> 标签供压制时按需下载
    emoji_map: dict[str, str] = {}
    for item in data_entries:
        for emo in item.get("customEmojis", []):
            name = emo.get("name", "").strip(":")
            url = emo.get("url", "")
            if name and url and name not in emoji_map:
                emoji_map[name] = url

    xml_lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<i>"]
    for name, url in sorted(emoji_map.items()):
        xml_lines.append(f'  <e name="{escape(name)}" url="{escape(url)}"/>')

    for item in data_entries:
        msg_dt = datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00"))
        offset = (msg_dt - start_dt).total_seconds()
        if offset < 0:
            continue
        text = item.get("message", "").strip()
        if not text:
            continue
        text = _EMOJI_PATTERN.sub(lambda m: f"[{m.group(1)}]", text)
        member = "1" if item.get("isMembership", False) else "0"
        author = item.get("author", "") or "匿名"
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
