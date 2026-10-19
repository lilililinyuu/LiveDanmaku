"""预览图生成：抽取原视频帧，叠加弹幕渲染层，输出 PNG。

用于在全量压制前快速确认弹幕样式效果。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from loguru import logger
from PySide6.QtGui import QImage, QPainter

from ..config.models import DEFAULT_CONFIG
from ..encode.ffmpeg import FFmpegManager
from ..errors import LiveDanmakuError
from ..input.parser import (
    align_first_event_time,
    drop_first_events,
    parse_subtitle_with_emoji,
)
from ..layout.engine import LayoutContext, LayoutEngine
from ..render.assets import AssetLoader
from .burner import danmaku_layout_context, danmaku_state_at


def extract_video_frame(video_in: str, time_sec: float) -> QImage | None:
    """用 ffmpeg 抽取视频在指定时间点的一帧（PNG 字节流）。"""
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", str(time_sec), "-i", str(video_in),
        "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-",
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        return None
    img = QImage()
    if img.loadFromData(result.stdout):
        return img
    return None


def default_preview_times(events, duration: float) -> list[float]:
    """自动挑选预览时间点：首条弹幕处 + 两个弹幕分布采样点。"""
    if not events:
        return [duration / 2] if duration > 0 else [0.0]
    times = [e.time for e in events]
    candidates = {max(0.0, times[0]), times[len(times) // 2], times[-1]}
    return sorted(candidates)


def _build_asset_loader(config):
    return AssetLoader(
        font_size=config.style.font_size,
        font_family=config.style.font_family,
        emoji_style=config.style.emoji,
        render_scale=config.style.render_scale,
    )


def render_preview_frame(
    video_in: str,
    t: float,
    events,
    remote_emoji,
    asset_provider,
    config,
    fps: float,
    w: int,
    h: int,
    pool,
    frame: QImage,
):
    """在已抽取的视频帧上叠加 t 时刻的弹幕层。

    仅当至少一条弹幕水平可见（部分进入画面）时返回 True。
    """
    row_height, top_margin, effective_rows = danmaku_layout_context(
        config, asset_provider, w, h,
    )
    ctx = LayoutContext()
    active = danmaku_state_at(
        pool, t, fps, ctx, asset_provider,
        effective_rows, w, row_height, top_margin,
        float(config.animation.scroll_speed),
    )
    visible = [dm for dm in active if 0 <= dm.x < w and dm.x + dm.total_width > 0]
    if not visible:
        return False
    painter = QPainter(frame)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    for dm in active:
        dm.render(painter, int(dm.x), int(dm.current_y))
    painter.end()
    return True


def generate_previews(
    video_in: str,
    subtitle_in: str,
    config=DEFAULT_CONFIG,
    times: list[float] | None = None,
    out_dir: Path | None = None,
    drop_first: int = 0,
) -> list[Path]:
    """生成预览图，返回输出文件路径列表。"""
    v_info = FFmpegManager.probe_video(video_in)
    fps = float(v_info["fps"])
    w = int(v_info["w"])
    h = int(v_info["h"])
    total_frames = int(v_info["frames"])
    duration = total_frames / fps if fps > 0 else 0

    events, remote_emoji = parse_subtitle_with_emoji(subtitle_in)
    if drop_first > 0:
        if drop_first >= len(events):
            raise LiveDanmakuError(
                f"删除条数 {drop_first} 不少于弹幕总数 {len(events)}（会删除全部弹幕）"
            )
        events = drop_first_events(events, drop_first)
        logger.info(f"已删除开头 {drop_first} 条弹幕，剩余 {len(events)} 条")
    if config.animation.first_danmaku_at is not None:
        align_first_event_time(events, config.animation.first_danmaku_at)
    if not events:
        raise LiveDanmakuError("弹幕文件没有任何事件")

    asset_provider = _build_asset_loader(config)
    asset_provider.load_assets(events, remote_emoji)
    pool = LayoutEngine.preload_danmaku_objects(
        events, int(w * config.ratio.text_width_ratio), asset_provider, config.style,
    )

    if times is None:
        times = default_preview_times(events, duration)
    out_dir = out_dir or (Path(video_in).parent / "preview")
    out_dir.mkdir(parents=True, exist_ok=True)

    paths: list[Path] = []
    for t in times:
        chosen: float | None = None
        frame: QImage | None = None
        # 该时刻无可见弹幕时向后微调（每 +1 秒重试，最多 5 次）
        for attempt in range(6):
            tt = t + attempt
            candidate = extract_video_frame(video_in, tt)
            if candidate is None:
                continue
            if render_preview_frame(
                video_in, tt, events, remote_emoji, asset_provider,
                config, fps, w, h, pool, candidate,
            ):
                chosen, frame = tt, candidate
                break
        if frame is None or chosen is None:
            logger.warning(f"t={t:.1f}s 附近没有可见弹幕，跳过")
            continue
        path = out_dir / f"preview_{chosen:.1f}s.png"
        frame.save(str(path))
        paths.append(path)
        logger.info(f"预览图: {path}")

    if not paths:
        raise LiveDanmakuError("未能生成任何预览图（时间点均无活跃弹幕）")
    return paths
