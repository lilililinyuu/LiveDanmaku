"""批量处理文件发现与字幕匹配。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from .utils.validation import SUPPORTED_VIDEO_EXTS

SUPPORTED_BATCH_SUBTITLE_EXTS = (".xml", ".lrc")


@dataclass(frozen=True, slots=True)
class BatchJob:
    """一条可执行的批量压制任务。"""

    video_path: Path
    subtitle_path: Path
    output_path: Path


@dataclass(frozen=True, slots=True)
class BatchSelection:
    """从用户选择的路径中整理出的批量处理结果。"""

    jobs: list[BatchJob]
    missing_subtitles: list[Path]
    ignored_paths: list[Path]


def default_output_path_for_video(video_path: Path) -> Path:
    """生成默认输出路径。"""
    return video_path.parent / f"{video_path.stem}-弹幕版.mp4"


def is_supported_video(path: Path) -> bool:
    """判断文件是否是支持的视频格式。"""
    try:
        return path.is_file() and path.suffix.lower() in SUPPORTED_VIDEO_EXTS
    except OSError as e:
        logger.warning("无法访问文件，已跳过: {} ({})", path, e)
        return False


def iter_videos_in_folder(folder_path: Path) -> list[Path]:
    """递归查找文件夹内所有支持的视频文件。"""
    if not folder_path.is_dir():
        return []

    videos: list[Path] = []
    pending = [folder_path]
    while pending:
        folder = pending.pop()
        try:
            children = list(folder.iterdir())
        except OSError as e:
            logger.warning("无法读取文件夹，已跳过: {} ({})", folder, e)
            continue

        for child in children:
            try:
                if child.is_dir() and not child.is_symlink():
                    pending.append(child)
                elif is_supported_video(child):
                    videos.append(child)
            except OSError as e:
                logger.warning("无法访问路径，已跳过: {} ({})", child, e)

    return sorted(videos, key=lambda path: str(path).lower())


def find_matching_subtitle(video_path: Path) -> Path | None:
    """查找同名字幕文件。

    优先选择同目录下同名的 XML；只有不存在 XML 时才选择 LRC。
    精确同名都不存在时，退而匹配 yt-dlp 导出的 `xxx.live_chat.xml/lrc`
    命名（对应视频 `xxx`）。
    """
    if not video_path.is_file():
        return None

    stem_key = video_path.stem.lower()
    live_chat_key = f"{stem_key}.live_chat"
    exact: dict[str, list[Path]] = {ext: [] for ext in SUPPORTED_BATCH_SUBTITLE_EXTS}
    live_chat: dict[str, list[Path]] = {ext: [] for ext in SUPPORTED_BATCH_SUBTITLE_EXTS}

    try:
        siblings = list(video_path.parent.iterdir())
    except OSError as e:
        logger.warning("无法读取字幕目录，已跳过: {} ({})", video_path.parent, e)
        return None

    for path in siblings:
        try:
            if not path.is_file():
                continue
        except OSError as e:
            logger.warning("无法访问字幕候选文件，已跳过: {} ({})", path, e)
            continue
        suffix = path.suffix.lower()
        if suffix not in exact:
            continue
        if path.stem.lower() == stem_key:
            exact[suffix].append(path)
        elif path.stem.lower() == live_chat_key:
            live_chat[suffix].append(path)

    for tier in (exact, live_chat):
        for suffix in SUPPORTED_BATCH_SUBTITLE_EXTS:
            matches = sorted(tier[suffix], key=lambda path: path.name.lower())
            if matches:
                return matches[0]

    return None


def collect_batch_jobs(paths: Iterable[str | Path]) -> BatchSelection:
    """根据用户选择的文件/文件夹收集批量任务。"""
    selected_paths = [Path(path).expanduser().resolve() for path in paths]
    video_map: dict[str, Path] = {}
    ignored_paths: list[Path] = []

    for path in selected_paths:
        if path.is_dir():
            for video_path in iter_videos_in_folder(path):
                video_map[str(video_path).lower()] = video_path
            continue

        if is_supported_video(path):
            video_map[str(path).lower()] = path
            continue

        ignored_paths.append(path)

    jobs: list[BatchJob] = []
    missing_subtitles: list[Path] = []
    for video_path in sorted(video_map.values(), key=lambda path: str(path).lower()):
        subtitle_path = find_matching_subtitle(video_path)
        if subtitle_path is None:
            missing_subtitles.append(video_path)
            continue

        jobs.append(BatchJob(
            video_path=video_path,
            subtitle_path=subtitle_path,
            output_path=default_output_path_for_video(video_path),
        ))

    return BatchSelection(
        jobs=jobs,
        missing_subtitles=missing_subtitles,
        ignored_paths=ignored_paths,
    )
