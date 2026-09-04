"""字幕解析器"""

from __future__ import annotations

import re
from pathlib import Path

from loguru import logger
from lxml import etree

from .event import DanmakuEvent

SUPPORTED_SUBTITLE_EXTS = frozenset({".xml", ".lrc"})

_LRC_TIMESTAMP_RE = re.compile(r"\[(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]")
_LRC_COLON_USER_RE = re.compile(r"^(?P<user>[^:：\t\r\n]{1,32})\s*[:：]\s*(?P<text>.+)$")
_LRC_TAB_USER_RE = re.compile(r"^(?P<user>[^\t\r\n]{1,32})\t+(?P<text>.+)$")
_LRC_WIDE_SPACE_USER_RE = re.compile(r"^(?P<user>\S.{0,31}?)\s{2,}(?P<text>\S.*)$")
_LRC_SINGLE_SPACE_USER_RE = re.compile(r"^(?P<user>\S{2,24})\s+(?P<text>\S.*)$")
_LRC_DISALLOWED_USER_CHARS = set("，。！？,.!?；;、()（）[]【】<>《》\"'")


def parse_xml(xml_path: str) -> list[DanmakuEvent]:
    events, _ = parse_xml_with_emoji(xml_path)
    return events


def parse_xml_with_emoji(xml_path: str) -> tuple[list[DanmakuEvent], dict[str, str]]:
    """解析 XML 弹幕，同时收集 <e name url> 表情元数据。

    Returns:
        (事件列表, 表情名 -> 图片 URL 映射)
    """
    events = []
    emoji_urls: dict[str, str] = {}
    for _event, elem in etree.iterparse(xml_path, events=('end',), tag=('d', 'e'), recover=True, encoding='utf-8'):
        if elem.tag == 'd':
            p_attr = elem.get('p')
            if p_attr:
                try:
                    comma_idx = p_attr.find(',')
                    time_val = float(p_attr[:comma_idx])
                    user = elem.get('user') or elem.get('uid') or "匿名"
                    text = elem.text or ""
                    member_attr = elem.get('member', '0')
                    is_membership = (member_attr == '1')
                    events.append(DanmakuEvent(
                        time=time_val,
                        user=user,
                        text=text,
                        is_membership=is_membership
                    ))
                except (ValueError, IndexError):
                    logger.debug(f"弹幕解析失败: p={p_attr}")
        elif elem.tag == 'e':
            name = elem.get('name')
            url = elem.get('url')
            if name and url and name not in emoji_urls:
                emoji_urls[name] = url
        elem.clear()
    events.sort(key=lambda e: e.time)
    return events, emoji_urls


def _lrc_timestamp_to_seconds(match):
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    fraction = (match.group(4) or "0").ljust(3, "0")[:3]
    milliseconds = int(fraction)
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000


def _has_cjk(text): return any("\u4e00" <= ch <= "\u9fff" for ch in text)
def _has_ascii_alpha(text): return any(("a" <= ch.lower() <= "z") for ch in text)

def _looks_like_lrc_username(user, *, allow_plain_cjk):
    user = user.strip()
    if not user or len(user) > 32:
        return False
    if any(ch.isspace() for ch in user):
        return False
    if any(ch in _LRC_DISALLOWED_USER_CHARS for ch in user):
        return False
    has_cjk = _has_cjk(user)
    has_ascii = _has_ascii_alpha(user)
    has_digit = any(ch.isdigit() for ch in user)
    has_user_symbol = any(ch in "_-@." for ch in user)
    if has_digit or has_user_symbol:
        return True
    if has_cjk and has_ascii:
        return True
    if has_ascii and user.upper() == user and len(user) >= 2:
        return True
    if allow_plain_cjk and has_cjk and 2 <= len(user) <= 16:
        return True
    return False

def _split_lrc_user_text(text):
    stripped = text.strip()
    if not stripped:
        return "", ""
    for pattern in (_LRC_COLON_USER_RE, _LRC_TAB_USER_RE, _LRC_WIDE_SPACE_USER_RE):
        match = pattern.match(stripped)
        if match:
            user = match.group("user").strip()
            body = match.group("text").strip()
            if user and body:
                return user, body
    match = _LRC_SINGLE_SPACE_USER_RE.match(stripped)
    if match:
        user = match.group("user").strip()
        body = match.group("text").strip()
        if body and _looks_like_lrc_username(user, allow_plain_cjk=True):
            return user, body
    return "", stripped

def parse_lrc(lrc_path):
    events = []
    try:
        with open(lrc_path, "r", encoding="utf-8-sig", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                matches = list(_LRC_TIMESTAMP_RE.finditer(line))
                if not matches:
                    continue
                text = _LRC_TIMESTAMP_RE.sub("", line).strip()
                if not text:
                    continue
                user, text = _split_lrc_user_text(text)
                if not text:
                    continue
                for match in matches:
                    events.append(DanmakuEvent(
                        time=_lrc_timestamp_to_seconds(match),
                        user=user,
                        text=text,
                    ))
    except OSError as e:
        logger.debug(f"LRC 读取失败: {lrc_path} ({e})")
        return []
    events.sort(key=lambda e: e.time)
    return events

def parse_subtitle(path):
    events, _ = parse_subtitle_with_emoji(path)
    return events


def parse_subtitle_with_emoji(path) -> tuple[list[DanmakuEvent], dict[str, str]]:
    """解析字幕文件，同时返回表情名 -> 图片 URL 映射（LRC 无表情，返回空映射）。"""
    suffix = Path(path).suffix.lower()
    if suffix == ".xml":
        return parse_xml_with_emoji(path)
    if suffix == ".lrc":
        return parse_lrc(path), {}
    raise ValueError(f"不支持的弹幕格式: {suffix}")


def align_first_event_time(events: list[DanmakuEvent], target_seconds: float) -> None:
    """整体平移事件时间，使最早一条事件出现在指定视频秒数。

    推迟（target 大于当前首条时间）或提前（target 更小，最小为 0）
    均按同一平移量移动全部事件，保持相互间隔不变。
    """
    if not events:
        return
    shift = target_seconds - min(e.time for e in events)
    if shift:
        for e in events:
            e.time += shift