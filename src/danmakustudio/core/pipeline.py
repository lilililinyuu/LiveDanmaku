"""yt-dlp 下载辅助函数（供图形界面的「下载内容」页使用）。"""

from __future__ import annotations

import shutil
import subprocess
import threading
from collections import deque
from collections.abc import Callable
from pathlib import Path

from loguru import logger

from ..errors import DanmakuStudioError

_YTDLP_FORMAT = "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b"
_VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".flv", ".mov", ".ts", ".avi")
_IMAGE_EXTS = (".webp", ".jpg", ".jpeg", ".png", ".avif", ".jfif", ".gif")

# Windows 的 GUI 进程没有可继承的控制台。显式禁止子进程创建新控制台窗口；
# 其他平台使用 0，保持 subprocess 的默认行为。
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def parse_ytdlp_paths(lines: list[str]) -> tuple[Path | None, Path | None]:
    """从 yt-dlp --print after_move:filepath 输出中解析视频与弹幕 JSON 路径。

    弹幕 JSON 优先匹配 *.live_chat.json；若输出中只有普通 .json
    （例如弹幕文件与视频同名、仅后缀不同），则接受该 .json。
    """
    video: Path | None = None
    jsons: list[Path] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        path = Path(line)
        if path.suffix.lower() == ".json":
            jsons.append(path)
        elif (
            video is None
            and path.suffix.lower() in _VIDEO_EXTS
        ):
            video = path
    chat: Path | None = next(
        (p for p in jsons if ".live_chat" in p.name), None
    )
    if chat is None and len(jsons) == 1:
        chat = jsons[0]
    return video, chat


def download_items(
    url: str,
    out_dir: Path,
    fmt: str,
    *,
    want_video: bool,
    want_chat: bool,
    want_thumbnail: bool,
    on_output: Callable[[str], None] | None = None,
    section: str | None = None,
) -> dict[str, Path | None]:
    """按需下载视频 / 实时弹幕 JSON / 封面，三者可任意组合。

    封面会统一转为 PNG 并清理原始图片。返回
    {'video': Path|None, 'chat': Path|None, 'thumbnail': Path|None}，
    未选择的项为 None。

    on_output: yt-dlp 每输出一行（含下载进度）时调用一次，用于界面日志。
    section: 下载指定片段（yt-dlp --download-sections），形如 "5:01-10:43"，
    单边界可用 "5:01-" / "-10:43"；None = 下载完整视频。
    """
    if not (want_video or want_chat or want_thumbnail):
        raise DanmakuStudioError("未选择任何要下载的内容。")
    if shutil.which("yt-dlp") is None:
        raise DanmakuStudioError(
            "未找到 yt-dlp，请先安装: pip install yt-dlp（或 winget install yt-dlp.yt-dlp）"
        )
    if want_thumbnail and shutil.which("ffmpeg") is None:
        raise DanmakuStudioError(
            "未找到 ffmpeg，无法把封面转换为 PNG，请先安装 ffmpeg。"
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    before = {p for p in out_dir.iterdir() if p.is_file()}

    cmd = ["yt-dlp", "--newline"]
    if section:
        cmd += ["--download-sections", f"*{section}"]
    if want_video:
        cmd += ["-f", fmt]
    else:
        cmd += ["--skip-download"]
    if want_chat:
        cmd += ["--write-subs", "--sub-langs", "live_chat"]
    if want_thumbnail:
        cmd += ["--write-thumbnail"]
    cmd += [
        "-o", str(out_dir / "%(title)s.%(ext)s"),
        "--print", "after_move:filepath",
        url,
    ]

    logger.info("下载视频/弹幕/封面...")
    # 流式读取 stdout：进度行即时回调给界面；stderr 由独立线程收集，
    # 避免管道缓冲区写满导致 yt-dlp 卡死
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=_CREATE_NO_WINDOW,
    )
    stdout_pipe = process.stdout
    stderr_pipe = process.stderr
    assert stdout_pipe is not None and stderr_pipe is not None
    stderr_tail: deque[str] = deque(maxlen=30)

    def _drain_stderr() -> None:
        for raw in stderr_pipe:
            line_str = raw.strip()
            if line_str:
                stderr_tail.append(line_str)

    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    stderr_thread.start()

    lines: list[str] = []
    for raw in stdout_pipe:
        line_str = raw.strip()
        if not line_str:
            continue
        if on_output is not None:
            on_output(line_str)
        lines.append(line_str)

    return_code = process.wait()
    stderr_thread.join(timeout=5)

    video, chat = parse_ytdlp_paths(lines)

    # --print after_move:filepath 只打印视频路径，不打印随视频写入的
    # 弹幕 JSON；stdout 中找不到时从下载目录的新增文件回退查找
    if want_chat and chat is None:
        chat = _find_new_file(out_dir, before, ".live_chat.json", ".json")
        if chat is not None:
            logger.info("从下载目录找到弹幕 JSON: {}", chat.name)
    if want_video and video is None:
        video = _find_new_file(out_dir, before, *_VIDEO_EXTS)
        if video is not None:
            logger.info("从下载目录找到视频: {}", video.name)

    thumbnail: Path | None = None
    if want_thumbnail:
        after = {p for p in out_dir.iterdir() if p.is_file()}
        thumbs = [p for p in (after - before) if p.suffix.lower() in _IMAGE_EXTS]
        if not thumbs:
            raise DanmakuStudioError("未找到下载的封面图片，请检查 yt-dlp 输出。")
        thumbnail = _largest_thumbnail_to_png(thumbs)

    # yt-dlp 异常退出时，只要有一部分请求的内容已成功获取，就按部分
    # 成功处理（例如视频中途失败但弹幕 JSON 已完整保存）
    if return_code != 0:
        tail = "\n".join(list(stderr_tail)[-10:])
        got_any = (
            (want_video and video is not None)
            or (want_chat and chat is not None)
            or (want_thumbnail and thumbnail is not None)
        )
        if not got_any:
            raise DanmakuStudioError(
                f"yt-dlp 下载失败: {tail or f'退出码 {return_code}'}"
            )
        logger.warning("yt-dlp 异常退出（退出码 {}），已获取部分内容", return_code)
        if on_output is not None:
            on_output(f"[警告] yt-dlp 异常退出（退出码 {return_code}），已获取部分内容")

    # 正常退出时所有请求项必须齐全；异常退出但拿到部分内容时按部分
    # 成功返回（缺失项为 None，由调用方按实际结果处理）
    if return_code == 0:
        if want_video and video is None:
            raise DanmakuStudioError("yt-dlp 输出中未找到视频文件，请检查下载结果。")
        if want_chat and chat is None:
            raise DanmakuStudioError("yt-dlp 输出中未找到弹幕 JSON，请检查下载结果。")

    return {"video": video, "chat": chat, "thumbnail": thumbnail}


def _safe_mtime(path: Path) -> float:
    """取文件 mtime；文件在扫描间隙被删除时返回 0（排序垫底）。"""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _find_new_file(
    out_dir: Path,
    before: set[Path],
    *preferred_suffixes: str,
) -> Path | None:
    """在下载目录的"新增文件"（下载前不存在）里按后缀优先级查找。

    后缀按传入顺序匹配：先找以 preferred_suffixes[0] 结尾的文件，
    再找 [1]……找到即返回（多个候选时取最新的一个）。
    """
    try:
        after = {p for p in out_dir.iterdir() if p.is_file()}
    except OSError as e:
        logger.warning("无法扫描下载目录: {} ({})", out_dir, e)
        return None
    new_files = sorted(
        after - before,
        key=lambda p: _safe_mtime(p),
        reverse=True,
    )
    for suffix in preferred_suffixes:
        for path in new_files:
            if path.name.lower().endswith(suffix):
                return path
    return None


def _largest_thumbnail_to_png(thumbs: list[Path]) -> Path:
    """从候选封面中取最大一张，统一转成 PNG 并清理其余原始图片。"""
    thumbs.sort(key=lambda p: p.stat().st_size, reverse=True)
    thumb = thumbs[0]
    if thumb.suffix.lower() == ".png":
        for extra in thumbs[1:]:
            extra.unlink(missing_ok=True)
        return thumb

    png_out = thumb.with_suffix(".png")
    conv = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(thumb), str(png_out),
    ]
    logger.info("封面转换为 PNG: {} -> {}", thumb.name, png_out.name)
    conv_result = subprocess.run(
        conv,
        capture_output=True,
        text=True,
        check=False,
        creationflags=_CREATE_NO_WINDOW,
    )
    if conv_result.returncode != 0 or not png_out.exists():
        raise DanmakuStudioError(
            f"封面转 PNG 失败: {conv_result.stderr[-400:] or '未生成输出文件'}"
        )
    # 清理所有下载的原始图片，只保留最终 PNG
    for p in thumbs:
        p.unlink(missing_ok=True)
    return png_out
