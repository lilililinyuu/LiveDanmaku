"""视频转 GIF：ffmpeg palettegen/paletteuse 两段式高质量 GIF 生成。

滤镜链：setpts(倍速) -> fps(帧率) -> scale(画幅) -> split -> palettegen +
paletteuse。调色板从片段自身提取（stats_mode=diff 兼顾运动画面），
质量档位映射调色板规模与抖动算法。
"""

from __future__ import annotations

import subprocess
import threading
from collections import deque
from collections.abc import Callable
from pathlib import Path

from loguru import logger

from ..encode.ffmpeg import FFmpegManager
from ..errors import LiveDanmakuError
from ..utils.validation import validate_video_input
from .pipeline import _CREATE_NO_WINDOW, _iter_output_lines, _wait_or_stop

# 质量档位 -> (palettegen 参数, paletteuse 参数)。
# reserve_transparent 预留透明索引、alpha_threshold=128 让 alpha=0 的
# 像素映射到透明索引（GIF 只有 1 位透明，半透明会被硬切到不透明）。
_QUALITY_PRESETS: dict[str, tuple[str, str]] = {
    "high": (
        "stats_mode=diff:reserve_transparent=on",
        "dither=sierra2_4a:alpha_threshold=128",
    ),
    "medium": (
        "stats_mode=diff:reserve_transparent=on",
        "dither=bayer:bayer_scale=4:alpha_threshold=128",
    ),
    "low": (
        "stats_mode=diff:max_colors=64:reserve_transparent=on",
        "dither=bayer:bayer_scale=5:alpha_threshold=128",
    ),
}

# 分辨率档位 -> 画幅宽度（高度按视频比例缩放）
_RESOLUTION_WIDTHS: dict[str, int] = {
    "original": 0,   # 保持原始宽度
    "1080p": 1920,
    "720p": 1280,
    "480p": 854,
    "360p": 640,
}


def _decoder_missing(tail: deque[str]) -> bool:
    """判断 stderr 是否为"解码器不存在"类错误。"""
    joined = "\n".join(tail).lower()
    return (
        "unknown decoder" in joined
        or "unrecognized decoder" in joined
        or "no decoder" in joined
    )


def probe_video_summary(video_in: str | Path) -> dict:
    """读取视频的时长/分辨率/帧率，供界面显示与时间轴钳制。"""
    info = FFmpegManager.probe_video(str(video_in))
    fps = float(info["fps"])
    frames = int(info["frames"])
    return {
        "duration": frames / fps if fps > 0 else 0.0,
        "width": int(info["w"]),
        "height": int(info["h"]),
        "fps": fps,
    }


def build_gif_command(
    video_in: Path,
    gif_out: Path,
    *,
    start: float,
    clip_duration: float,
    speed: float,
    fps: int,
    max_width: int,
    palettegen_opts: str,
    paletteuse_opts: str,
    force_vpx_decoder: bool = False,
) -> list[str]:
    """构建 ffmpeg 转换命令（独立成函数供测试断言）。"""
    width_expr = str(max_width) if max_width > 0 else "iw"
    palettegen = f"palettegen={palettegen_opts}" if palettegen_opts else "palettegen"
    paletteuse = f"paletteuse={paletteuse_opts}" if paletteuse_opts else "paletteuse"
    vf = (
        f"setpts=PTS/{speed},"
        f"fps={fps},"
        f"scale={width_expr}:-2:flags=lanczos,"
        f"format=rgba,"
        f"split[a][b];[a]{palettegen}[p];[b][p]{paletteuse}"
    )
    prefix: list[str] = []
    if force_vpx_decoder:
        # WebM 的 VP9/VP8 alpha 存在 BlockAdditions 附加层里，
        # 原生解码器会忽略（透明背景变黑），必须用 libvpx 解码器合并
        prefix = ["-c:v", "libvpx-vp9"]
    return [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-nostats", "-progress", "pipe:1",
        *prefix,
        "-ss", f"{start:.3f}", "-t", f"{clip_duration:.3f}",
        "-i", str(video_in),
        "-vf", vf,
        "-loop", "0", "-an",
        str(gif_out),
    ]


def make_gif(
    video_in: str | Path,
    gif_out: str | Path | None = None,
    *,
    start: float = 0.0,
    end: float | None = None,
    speed: float = 1.0,
    fps: int = 15,
    resolution: str = "original",
    quality: str = "medium",
    force: bool = False,
    on_output: Callable[[str], None] | None = None,
    stop_flag: Callable[[], bool] | None = None,
) -> dict:
    """把视频片段转换为 GIF。

    Args:
        video_in: 输入视频
        gif_out: 输出 GIF 路径；None = 视频同目录 `<名字>.gif`
        start: 片段开始（视频秒数）
        end: 片段结束（视频秒数）；None = 视频结尾
        speed: 播放倍速（0.25~4，>1 加速）
        fps: GIF 帧率（5~30）
        resolution: 画幅分辨率档位 "original" / "1080p" / "720p" /
            "480p" / "360p"（高度按视频比例缩放）
        quality: 调色板/抖动档位 "high" / "medium" / "low"
        force: 输出已存在时覆盖
        on_output: ffmpeg 进度行回调
        stop_flag: 返回 True 时终止转换；部分结果被清理，
            返回 {"gif": None, "stopped": True}

    Returns:
        {"gif": Path|None, "start": float, "clip_duration": float,
         "output_duration": float, "stopped": bool}
    """
    if quality not in _QUALITY_PRESETS:
        raise LiveDanmakuError(
            f"调色板档位只能是 {'/'.join(_QUALITY_PRESETS)}，当前 {quality}"
        )
    if resolution not in _RESOLUTION_WIDTHS:
        raise LiveDanmakuError(
            f"分辨率只能是 {'/'.join(_RESOLUTION_WIDTHS)}，当前 {resolution}"
        )
    if not 0.25 <= speed <= 4.0:
        raise LiveDanmakuError(f"倍速必须在 0.25~4.0 之间，当前 {speed}")
    if not 5 <= fps <= 30:
        raise LiveDanmakuError(f"帧率必须在 5~30 之间，当前 {fps}")

    video = Path(video_in)
    validate_video_input(str(video))

    info = probe_video_summary(video)
    duration = float(info["duration"])
    if duration <= 0:
        raise LiveDanmakuError("无法读取视频时长，无法转换 GIF。")

    start = max(0.0, min(float(start), duration))
    end = duration if end is None else min(float(end), duration)
    if end - start < 0.1:
        raise LiveDanmakuError(
            f"片段长度太短（{end - start:.2f} 秒）：请设置有效的开始/结束时间。"
        )
    clip_duration = end - start

    if gif_out is None:
        out = video.with_name(f"{video.stem}-{start:g}-{end:g}s.gif")
    else:
        out = Path(gif_out)
    if out.suffix.lower() != ".gif":
        out = out.with_suffix(".gif")
    if out.exists() and not force:
        raise LiveDanmakuError(f"输出文件已存在: {out}（勾选覆盖或更换文件名）")

    palettegen_opts, paletteuse_opts = _QUALITY_PRESETS[quality]
    max_width = _RESOLUTION_WIDTHS[resolution]
    force_vpx = video.suffix.lower() in (".webm",)

    def _build(force_decoder: bool) -> list[str]:
        return build_gif_command(
            video, out,
            start=start, clip_duration=clip_duration,
            speed=speed, fps=fps, max_width=max_width,
            palettegen_opts=palettegen_opts, paletteuse_opts=paletteuse_opts,
            force_vpx_decoder=force_decoder,
        )

    cmd = _build(force_vpx)
    logger.info("启动 ffmpeg (GIF): {}", " ".join(cmd))

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_CREATE_NO_WINDOW,
    )
    assert process.stdout is not None and process.stderr is not None
    tail: deque[str] = deque(maxlen=30)

    def _drain_out() -> None:
        for raw in _iter_output_lines(process.stdout):
            line_str = raw.strip()
            if line_str and on_output is not None:
                on_output(line_str)

    def _drain_err() -> None:
        for raw in _iter_output_lines(process.stderr):
            line_str = raw.strip()
            if line_str:
                tail.append(line_str)

    threading.Thread(target=_drain_out, daemon=True).start()
    threading.Thread(target=_drain_err, daemon=True).start()

    returncode, was_stopped = _wait_or_stop(process, stop_flag)

    # 构建不含 libvpx 解码器时，强制解码器会启动失败：回退无 -c:v 重跑
    if force_vpx and returncode != 0 and was_stopped is False and _decoder_missing(tail):
        logger.warning("libvpx-vp9 解码器不可用，回退默认解码器重试（alpha 可能丢失）")
        cmd = _build(False)
        logger.info("启动 ffmpeg (GIF 回退): {}", " ".join(cmd))
        tail.clear()
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_CREATE_NO_WINDOW,
        )
        assert process.stdout is not None and process.stderr is not None
        threading.Thread(target=_drain_out, daemon=True).start()
        threading.Thread(target=_drain_err, daemon=True).start()
        returncode, was_stopped = _wait_or_stop(process, stop_flag)

    if was_stopped:
        out.unlink(missing_ok=True)
        logger.warning("GIF 转换已被用户停止，半成品已清理")
        return {
            "gif": None, "start": start, "clip_duration": clip_duration,
            "output_duration": clip_duration / speed, "stopped": True,
        }

    if returncode != 0:
        raise LiveDanmakuError(
            "GIF 转换失败（退出码 {}）: {}".format(
                returncode, "\n".join(list(tail)[-10:]) or "无输出"
            )
        )
    if not out.exists() or out.stat().st_size == 0:
        raise LiveDanmakuError(
            "GIF 转换失败: 未生成输出文件。"
            + ("\n".join(list(tail)[-10:]) if tail else "")
        )

    logger.info("GIF 转换完成: {} ({:.1f} MB)", out, out.stat().st_size / 2**20)
    return {
        "gif": out, "start": start, "clip_duration": clip_duration,
        "output_duration": clip_duration / speed, "stopped": False,
    }
