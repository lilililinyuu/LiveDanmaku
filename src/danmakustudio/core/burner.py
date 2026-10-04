"""弹幕压制核心引擎"""

from __future__ import annotations

import math
import sys
import time
from collections.abc import Callable
from pathlib import Path
from time import monotonic, perf_counter

from loguru import logger
from tqdm import tqdm

from ..config.models import DEFAULT_CONFIG, EncodeMode
from ..encode.ffmpeg import FFmpegManager
from ..errors import DanmakuStudioError, EncodeError, EncoderStalledError, InputError
from ..input.parser import (
    align_first_event_time,
    drop_first_events,
    parse_subtitle_with_emoji,
)
from ..layout.engine import LayoutContext, LayoutEngine
from ..render.assets import AssetLoader
from ..render.renderer import DanmakuRenderer
from ..utils.validation import (
    validate_output_path,
    validate_subtitle_input,
    validate_video_input,
)

ProgressCallback = Callable[[int, int, float, float], None]

def _format_duration(seconds):
    seconds = max(0, int(seconds))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"

def _stderr_is_tty():
    isatty = getattr(sys.stderr, "isatty", None)
    return bool(isatty and isatty())

def danmaku_layout_context(config, asset_provider, w, h):
    """计算弹幕渲染的布局上下文（行高、顶部边距、有效行数）。"""
    style = config.style
    ratio = config.ratio
    row_height = asset_provider.line_height + style.row_vertical_gap
    top_margin = ratio.top_margin
    visible_height = max(1, h - ratio.top_margin - ratio.bottom_margin)
    effective_rows = max(1, min(ratio.max_text_rows, visible_height // row_height))
    return row_height, top_margin, effective_rows

def danmaku_state_at(
    pool, t, fps, ctx, asset_provider,
    max_text_rows, video_width, row_height, text_top, scroll_speed,
):
    """直算 t 时刻的活跃弹幕列表与车道占用状态（与逐帧模拟等价）。

    发射时刻按帧网格量化（ceil(time*fps)/fps），位置按
    video_width - scroll_speed*(t - 出生时刻) 计算，已滚出左侧的不进入列表。
    """
    active = []
    idx = 0
    while idx < len(pool) and pool[idx].event.time <= t:
        dm = pool[idx]
        spawn_time = math.ceil(dm.event.time * fps) / fps
        if dm.cached_image is None:
            dm.pre_render(
                asset_provider.font,
                asset_provider.emoji_cache,
                scale=getattr(asset_provider, "render_scale", 1.0),
            )
        row = LayoutEngine.pick_lane(ctx, spawn_time, dm, max_text_rows, scroll_speed)
        dm.target_y = text_top + row * row_height
        dm.current_y = dm.target_y
        dm.x = video_width - scroll_speed * (t - spawn_time)
        if dm.x + dm.total_width >= 0:
            active.append(dm)
        idx += 1
    ctx.event_idx = idx
    return active

class DanmakuBurner:
    def __init__(self, video_in, xml_in, video_out=None, encode_mode=EncodeMode.AUTO, config=DEFAULT_CONFIG, force=False, progress_callback=None, clip=None, drop_first=0):
        validate_video_input(video_in)
        validate_subtitle_input(xml_in)
        self.video_in = video_in
        self.xml_in = xml_in
        self.subtitle_in = xml_in
        self._config = config
        self._progress_callback = progress_callback
        self._clip = clip
        if drop_first < 0:
            raise InputError(f"删除条数不能为负数，当前 {drop_first}")
        self._drop_first = drop_first
        if video_out:
            self.video_out = video_out
        else:
            video_path = Path(video_in)
            self.video_out = str(video_path.parent / f"{video_path.stem}-弹幕版.mp4")
        validate_output_path(self.video_out, force)
        self._render_scale = max(1.0, float(self._config.style.render_scale))
        self._asset_provider = AssetLoader(
            font_size=self._config.style.font_size,
            font_family=self._config.style.font_family,
            emoji_style=self._config.style.emoji,
            render_scale=self._render_scale,
        )
        self._frame_encoder = FFmpegManager(video_in, self.video_out, encode_mode, self._config.encode, self._config.system)

    def run(self):
        """执行压制；QSV 等硬件编码器停转时自动改用 CPU 重试一次。"""
        try:
            self._run_once()
        except EncoderStalledError as e:
            if self._encode_mode == EncodeMode.CPU:
                raise
            logger.warning(f"{e}；自动改用 CPU 编码重试")
            self._encode_mode = EncodeMode.CPU
            self._frame_encoder = FFmpegManager(
                self.video_in, self.video_out, EncodeMode.CPU,
                self._config.encode, self._config.system,
            )
            self._run_once()

    def _run_once(self):
        cfg = self._config
        style = cfg.style
        syscfg = cfg.system

        events, remote_emoji = parse_subtitle_with_emoji(self.subtitle_in)
        if self._drop_first > 0:
            if self._drop_first >= len(events):
                raise InputError(
                    f"删除条数 {self._drop_first} 不少于弹幕总数 {len(events)}（会删除全部弹幕）"
                )
            events = drop_first_events(events, self._drop_first)
            logger.info(f"已删除开头 {self._drop_first} 条弹幕，剩余 {len(events)} 条")
        if cfg.animation.first_danmaku_at is not None:
            align_first_event_time(events, cfg.animation.first_danmaku_at)
        subtitle_type = Path(self.subtitle_in).suffix.upper().lstrip(".")
        logger.info(f"[1/8] 解析 {subtitle_type}: {len(events)} 条事件")

        self._asset_provider.load_assets(events, remote_emoji)
        logger.info(f"[2/8] 加载资源: Emoji {len(self._asset_provider.emoji_cache)}")

        v_info = self._frame_encoder.get_video_info()
        raw_w = int(v_info['w'])
        raw_h = int(v_info['h'])
        fps = float(v_info['fps'])
        total_frames = int(v_info['frames'])
        align = syscfg.video_alignment
        w = int(((raw_w + align - 1) // align) * align)
        h = int(((raw_h + align - 1) // align) * align)
        duration = total_frames / fps if fps > 0 else 0
        logger.info(f"[3/8] 视频信息: {raw_w}x{raw_h}, {fps:.2f} fps, {total_frames} 帧, 时长 {_format_duration(duration)}")
        if (w, h) != (raw_w, raw_h):
            logger.info(f"[3/8] 画面尺寸对齐: {raw_w}x{raw_h} -> {w}x{h}")

        text_w, layer_params = LayoutEngine.calculate_params(
            w, h, style, cfg.ratio,
        )
        logger.info("[4/8] 计算布局参数")

        danmaku_pool = LayoutEngine.preload_danmaku_objects(events, text_w, self._asset_provider, style)
        logger.info(f"[5/8] 预创建弹幕对象: {len(danmaku_pool)} 个")

        if self._clip is not None:
            clip_start, clip_duration = self._clip
            if clip_start is None:
                clip_start = events[0].time if events else 0.0
            clip_start = max(0.0, min(clip_start, duration))
            clip_end = min(duration, clip_start + clip_duration)
            start_frame = int(clip_start * fps)
            end_frame = min(total_frames, math.ceil(clip_end * fps))
            in_ss = start_frame / fps
            in_t = (end_frame - start_frame) / fps
            logger.info(
                f"[6/8] 测试片段: {in_ss:.1f}s 起 {in_t:.1f}s, 帧 {start_frame}-{end_frame}"
            )
        else:
            start_frame, end_frame = 0, total_frames
            in_ss, in_t = 0.0, None

        ffmpeg_cmd = self._frame_encoder.build_command(
            fps, w, h, layer_params, in_ss, in_t,
            render_scale=self._render_scale,
        )
        logger.info("[6/8] 构建编码命令")

        run_failed = False
        try:
            self._frame_encoder.start(ffmpeg_cmd)
            logger.info("[7/8] 启动 FFmpeg: 已启动")
            logger.complete()
            self._render_loop(
                fps, total_frames, danmaku_pool, layer_params, video_width=w,
                start_frame=start_frame, end_frame=end_frame,
            )
        except KeyboardInterrupt:
            run_failed = True
            logger.warning("用户中断压制")
            raise
        except DanmakuStudioError:
            run_failed = True
            raise
        except Exception as e:
            run_failed = True
            raise RuntimeError(f"压制失败: {e}") from e
        finally:
            logger.debug("清理资源")
            try:
                self._frame_encoder.cleanup()
            except DanmakuStudioError:
                if run_failed:
                    logger.exception("FFmpeg 清理阶段也发生错误")
                else:
                    raise

    def _encode_frame(self, frame_encoder, renderer, pbar, frame_idx, total_frames):
        try:
            frame_encoder.submit_frame(renderer.get_frame_data())
            pbar.update(1)
        except (BrokenPipeError, OSError, RuntimeError) as e:
            raise EncodeError(f"编码器错误 [帧 {frame_idx}]: {e}") from e

    def _render_loop(self, fps, total_frames, danmaku_pool, layer_params, video_width, start_frame=0, end_frame=None):
        end_frame = total_frames if end_frame is None else end_frame
        seg_frames = end_frame - start_frame
        frame_encoder = self._frame_encoder

        layout_ctx = LayoutContext()
        renderer = DanmakuRenderer(layer_params, scale=self._render_scale)

        total_text_danmaku = len(danmaku_pool)

        logger.info(f"[8/8] 渲染压制: 弹幕 {total_text_danmaku}, 帧 {start_frame}-{end_frame} ({seg_frames} 帧)")
        logger.complete()

        show_progress_bar = _stderr_is_tty()
        pbar = tqdm(total=seg_frames, desc="压制进度", unit="帧", disable=not show_progress_bar)

        started_at = perf_counter()
        last_log_at = started_at
        last_callback_at = started_at
        progress_log_interval = 5.0
        progress_callback_interval = 0.5

        scroll_speed = 300
        row_height, top_margin, effective_rows = danmaku_layout_context(
            self._config, self._asset_provider, video_width,
            layer_params.layer_h + self._config.ratio.bottom_margin,
        )

        # 片段起点晚于 0：直算起点时刻的活跃弹幕状态（不渲染）
        active_text = []
        if start_frame > 0:
            active_text = danmaku_state_at(
                danmaku_pool, start_frame / fps, fps, layout_ctx,
                self._asset_provider, effective_rows, video_width,
                row_height, top_margin, scroll_speed,
            )
        total_text_spawned = layout_ctx.event_idx

        if self._progress_callback:
            self._progress_callback(0, seg_frames, 0.0, 0.0)

        def report_progress(done_frames, force=False):
            nonlocal last_log_at, last_callback_at
            now = perf_counter()
            elapsed = now - started_at
            percent = (done_frames / seg_frames * 100.0) if seg_frames else 100.0
            if self._progress_callback and (force or now - last_callback_at >= progress_callback_interval):
                self._progress_callback(done_frames, seg_frames, percent, elapsed)
                last_callback_at = now
            if show_progress_bar:
                return
            should_log = force or done_frames == seg_frames or now - last_log_at >= progress_log_interval
            if not should_log:
                return
            speed = done_frames / elapsed if elapsed > 0 else 0.0
            remaining = (seg_frames - done_frames) / speed if speed > 0 else 0.0
            logger.info(f"进度: {percent:5.1f}% ({done_frames}/{seg_frames} 帧), 速度 {speed:.1f} 帧/s, 已用 {_format_duration(elapsed)}, 剩余 {_format_duration(remaining)}")
            last_log_at = now

        # 背压：产出快于编码消费时暂停渲染。ffmpeg 的帧队列无界，
        # 无节流时 1080p60 约 32 秒即可耗尽系统内存（实测整机崩溃）。
        # 高水位 4 秒、低水位 1 秒（按输出帧计）；编码器 60 秒无进展则中止。
        high_watermark = int(fps * 4)
        low_watermark = int(fps * 1)
        stall_state = [0, monotonic()]
        pausing = [False]

        try:
            frame_idx = start_frame
            while frame_idx < end_frame:
                current_time = frame_idx / fps
                dt = 1.0 / fps

                encoded = frame_encoder.encoded_frames
                backlog = (frame_idx - start_frame + 1) - encoded
                if backlog > high_watermark or (pausing[0] and backlog > low_watermark):
                    if encoded != stall_state[0]:
                        stall_state[0] = encoded
                        stall_state[1] = monotonic()
                    elif monotonic() - stall_state[1] > 60.0:
                        raise EncoderStalledError(
                            "编码器 60 秒无进展（无法消费帧），已中止压制"
                        )
                    if not pausing[0]:
                        pausing[0] = True
                        logger.info(
                            "渲染快于编码速度，已自动节流"
                            "（进度以实际编码完成帧数为准）"
                        )
                    # 节流期间按编码完成帧数刷新进度，让界面持续可见
                    report_progress(min(encoded, seg_frames))
                    pbar.n = min(encoded, seg_frames)
                    pbar.refresh()
                    time.sleep(0.02)
                    continue   # 原地等待同一帧（不丢帧）
                if pausing[0]:
                    pausing[0] = False
                    logger.info("积压已消化，恢复正常渲染速度")

                text_emitted = LayoutEngine.spawn_new_danmakus(
                    layout_ctx, current_time, danmaku_pool, active_text,
                    self._asset_provider,
                    max_text_rows=effective_rows,
                    video_width=video_width,
                    row_height=row_height,
                    text_top=top_margin,
                    scroll_speed=scroll_speed,
                )
                total_text_spawned += text_emitted

                for dm in active_text:
                    dm.x -= scroll_speed * dt

                LayoutEngine.recycle_offscreen(active_text)

                renderer.render_frame(active_text)

                self._encode_frame(frame_encoder, renderer, pbar, frame_idx, seg_frames)
                report_progress(frame_idx - start_frame + 1)
                frame_idx += 1
        finally:
            renderer.end()
            pbar.close()

        elapsed = perf_counter() - started_at
        report_progress(seg_frames, force=True)
        logger.info(f"完成: {seg_frames} 帧, 弹幕 {total_text_spawned}/{total_text_danmaku}, 用时 {_format_duration(elapsed)}")
        if total_text_spawned < total_text_danmaku:
            logger.warning(f"文本弹幕未完全发射: {total_text_spawned}/{total_text_danmaku}")