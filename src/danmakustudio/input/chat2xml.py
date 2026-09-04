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

_EMOJI_PATTERN = re.compile(r":([^:\s]+):")


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
) -> dict:
    """把 YouTube 直播回放 JSON 转为弹幕 XML。

    Args:
        json_path: 输入 JSON 文件（JSONL 格式的 live_chat）
        xml_path: 输出 XML 文件
        lead_in: 第一条弹幕相对视频开始的时间（秒）

    Returns:
        统计信息：{"messages": 条数, "start_time": 视频起始时间 ISO, "lead_in": 秒}
    """
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
        "start_time": start_dt.isoformat(),
        "lead_in": lead_in,
    }
