"""弹幕文件开头删除：直接生成删除后的 JSON/XML 文件。

与压制时的"删除开头弹幕"不同，这里会把删除结果写成真实文件：
- 聊天 JSON（JSONL）：按行保留原始内容，仅跳过开头 N 条消息行，
  非消息对象（如有）原样保留；
- 弹幕 XML：移除前 N 个 <d> 元素，<e> 表情元数据等其它内容保持不变。

所有校验在写入前完成，覆盖原文件时不会因中途出错而损坏源文件。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from loguru import logger
from lxml import etree


def safe_xml_parser() -> etree.XMLParser:
    """禁用外部实体与网络加载的 XML 解析器。

    弹幕 XML 不需要 DTD/实体：来自第三方工具的字幕文件不可信，
    关闭实体解析可杜绝 XXE（外部实体读本地文件/网络请求）类攻击。
    """
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        attribute_defaults=False,
        huge_tree=False,
    )


def default_trimmed_path(src: Path, drop_first: int) -> Path:
    """生成新文件模式下的默认输出路径：`原名-删开头N条.扩展名`。

    yt-dlp 的 `xxx.live_chat.json` 命名会去掉 `.live_chat` 中缀，
    与转换产物的命名规则保持一致（`xxx-删开头N条.json`）。
    """
    stem = src.stem
    stem = stem.removesuffix(".live_chat")
    return src.with_name(f"{stem}-删开头{drop_first}{src.suffix}")


def trim_chat_json_file(src: Path, dst: Path, drop_first: int) -> tuple[int, int]:
    """删除聊天 JSONL 开头 drop_first 条消息，结果写入 dst。

    返回 (删除条数, 保留行数)。保留行按原始文本写出，非消息对象
    原样保留。drop_first 超过消息总数时抛 ValueError（不写任何文件）。

    dst 允许与 src 相同（覆盖原文件）：先写临时文件再原子替换，
    避免边读边写把源文件清空。
    """
    if drop_first < 0:
        raise ValueError(f"删除条数不能为负数，当前 {drop_first}")

    src = Path(src).resolve()
    dst = Path(dst).resolve()
    # 纵深防御断言：resolve() 已消除 .. 段，此处再显式校验一次
    for label, p in (("源文件", src), ("目标文件", dst)):
        if ".." in p.parts:
            raise ValueError(f"{label}路径包含非法的 .. 段: {p}")
    if not src.is_file():
        raise ValueError(f"弹幕文件不存在: {src}")
    total = _count_chat_messages(src)
    if drop_first >= total:
        raise ValueError(
            f"删除条数 {drop_first} 不少于消息总数 {total}（会删除全部弹幕）"
        )

    removed = 0
    kept = 0
    tmp = dst.with_name(dst.name + ".tmp")
    try:
        with open(src, "r", encoding="utf-8") as fin, open(
            tmp, "w", encoding="utf-8"
        ) as fout:
            for line in fin:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    obj = json.loads(stripped)
                except json.JSONDecodeError:
                    keep = True
                else:
                    is_message = isinstance(obj, dict) and bool(
                        obj.get("replayChatItemAction")
                    )
                    if is_message and removed < drop_first:
                        removed += 1
                        keep = False
                    else:
                        keep = True
                if keep:
                    fout.write(line if line.endswith("\n") else line + "\n")
                    kept += 1
        os.replace(tmp, dst)
    except BaseException:
        # 失败时清理残留的临时文件（成功时临时文件已被替换移走）
        tmp.unlink(missing_ok=True)
        raise
    logger.info("聊天 JSON 开头删除: 删除 {} 条，保留 {} 行 -> {}", removed, kept, dst)
    return removed, kept


def trim_subtitle_xml_file(src: Path, dst: Path, drop_first: int) -> tuple[int, int]:
    """删除弹幕 XML 开头 drop_first 条 <d> 弹幕，结果写入 dst。

    <e> 表情元数据等其它元素保持不变。返回 (删除条数, 保留弹幕数)。
    drop_first 超过弹幕总数时抛 ValueError（不写任何文件）。
    """
    if drop_first < 0:
        raise ValueError(f"删除条数不能为负数，当前 {drop_first}")

    # 弹幕 XML 不需要 DTD/实体：禁用外部实体解析（防 XXE 读本地文件）
    safe_parser = etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False
    )
    tree = etree.parse(str(src), safe_parser)
    root = tree.getroot()
    danmakus = root.findall("d")
    if drop_first >= len(danmakus):
        raise ValueError(
            f"删除条数 {drop_first} 不少于弹幕总数 {len(danmakus)}（会删除全部弹幕）"
        )
    for elem in danmakus[:drop_first]:
        root.remove(elem)
    tree.write(str(dst), xml_declaration=True, encoding="UTF-8")
    kept = len(danmakus) - drop_first
    logger.info("弹幕 XML 开头删除: 删除 {} 条，保留 {} 条 -> {}", drop_first, kept, dst)
    return drop_first, kept


def _count_chat_messages(src: Path) -> int:
    """统计聊天 JSONL 中的消息行数。"""
    total = 0
    with open(src, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and obj.get("replayChatItemAction"):
                total += 1
    return total
