"""GUI 后台批量压制线程。"""

from __future__ import annotations

import re
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from time import perf_counter

from loguru import logger
from PySide6.QtCore import QObject, QThread, Signal

from ..batch import BatchJob
from ..config.loader import load_config
from ..config.models import EncodeMode, StyleOverrides, apply_style_overrides
from ..core.burner import DanmakuBurner
from ..errors import LiveDanmakuError, handle_error
from ..logger_config import configure_logger
from ._shared import format_duration

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class QtLogStream:
    """把 stdout/stderr 文本转发到 Qt signal。"""

    def __init__(self, log_signal: Signal):
        self.log_signal = log_signal

    def write(self, text: str) -> None:
        if not text:
            return

        for line in text.replace("\r", "\n").splitlines():
            cleaned = ANSI_ESCAPE_RE.sub("", line).strip()
            if not cleaned:
                continue
            try:
                self.log_signal.emit(cleaned)
            except RuntimeError:
                # 信号源 QObject 已销毁（界面关闭/worker 被替换）：
                # 丢弃残余日志，避免 loguru 每条都打 Logging error
                pass

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False


class GuiLogBridge(QObject):
    """应用生命周期的日志桥。

    configure_logger 会把"当时的 sys.stderr"对象捕获为 loguru 的
    stderr sink。若绑定到某个 worker 的信号，worker 被替换或销毁后
    sink 仍持有已删除的 Qt 信号源，之后所有日志（下载/录制的
    pipeline 日志）都会抛 RuntimeError 并断流。绑定到与界面同寿命
    的桥对象可避免。
    """

    log = Signal(str)


_bridge_lock = threading.Lock()
_gui_log_bridge: GuiLogBridge | None = None


def get_gui_log_bridge() -> GuiLogBridge:
    """返回全局唯一的日志桥（与主界面同寿命）。"""
    global _gui_log_bridge
    with _bridge_lock:
        if _gui_log_bridge is None:
            _gui_log_bridge = GuiLogBridge()
        return _gui_log_bridge


class BatchWorker(QThread):
    """后台批量压制线程。"""

    log = Signal(str)
    progress = Signal(int, int, float, float)
    summary = Signal(int, int, int, int)

    def __init__(
        self,
        jobs: list[BatchJob],
        encode_mode: EncodeMode,
        config_path: str | None,
        force: bool,
        style_overrides: StyleOverrides | None = None,
        first_danmaku_at: float | None = None,
        scroll_speed: float | None = None,
        ffmpeg_args: str | None = None,
        test_clip: bool = False,
        clip_duration: float = 60.0,
        drop_first: int = 0,
        clip_start: float | None = None,
        render_scale: float | None = None,
    ):
        super().__init__()
        self.jobs = jobs
        self.render_scale = render_scale
        self.encode_mode = encode_mode
        self.config_path = config_path
        self.force = force
        self.style_overrides = style_overrides
        self.first_danmaku_at = first_danmaku_at
        self.scroll_speed = scroll_speed
        self.ffmpeg_args = ffmpeg_args
        self.test_clip = test_clip
        self.clip_duration = clip_duration
        self.drop_first = drop_first
        self.clip_start = clip_start

    def run(self) -> None:
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        # 绑定到应用级日志桥而非 self.log：worker 对象会被后续任务替换
        # 销毁，而 loguru 的 stderr sink 在 configure_logger 时捕获该
        # stream，绑定到桥可保证 sink 永远持有存活对象
        stream = QtLogStream(get_gui_log_bridge().log)
        sys.stdout = stream
        sys.stderr = stream

        total = len(self.jobs)
        generated = 0
        skipped = 0
        failed = 0
        batch_started_at = perf_counter()

        try:
            configure_logger()
            config = load_config(self.config_path)
            if self.style_overrides is not None:
                config = apply_style_overrides(config, self.style_overrides)
                self.log.emit("样式: 使用界面自定义样式覆盖")
            if self.render_scale is not None:
                config = replace(
                    config,
                    style=replace(config.style, render_scale=self.render_scale),
                )
                self.log.emit(f"弹幕渲染精度: {self.render_scale:g} 倍")
            if self.first_danmaku_at is not None:
                config = replace(
                    config,
                    animation=replace(
                        config.animation, first_danmaku_at=self.first_danmaku_at
                    ),
                )
                self.log.emit(f"首条弹幕时间: {self.first_danmaku_at} 秒")
            if self.scroll_speed is not None:
                config = replace(
                    config,
                    animation=replace(
                        config.animation, scroll_speed=self.scroll_speed
                    ),
                )
                self.log.emit(f"弹幕流速: {self.scroll_speed:g} px/s")
            if self.ffmpeg_args:
                import shlex
                config = replace(
                    config,
                    encode=replace(
                        config.encode,
                        extra_args=config.encode.extra_args + shlex.split(self.ffmpeg_args),
                    ),
                )
                self.log.emit(f"自定义参数: {self.ffmpeg_args}")
            if self.drop_first:
                self.log.emit(f"删除开头弹幕: 前 {self.drop_first} 条")
            if self.test_clip:
                start_desc = (
                    f"从 {self.clip_start:g} 秒" if self.clip_start is not None else "从首条弹幕处"
                )
                self.log.emit(
                    f"测试片段模式: {start_desc}开始 {self.clip_duration:g} 秒"
                )
            self.log.emit(f"总计 {total} 个任务")
            if self.config_path:
                self.log.emit(f"配置文件: {self.config_path}")
            else:
                self.log.emit("配置文件: 默认配置")
            self.log.emit(f"编码模式: {self.encode_mode}")

            for index, job in enumerate(self.jobs, start=1):
                job_started_at = perf_counter()
                if self.test_clip:
                    job_output = job.video_path.parent / f"{job.video_path.stem}.test-clip.mp4"
                    clip = (self.clip_start, self.clip_duration)
                else:
                    job_output = job.output_path
                    clip = None

                self.log.emit("")
                self.log.emit("-" * 56)
                self.log.emit(f"任务 {index}/{total}: {job.video_path.name}")
                self.log.emit(f"视频: {job.video_path}")
                self.log.emit(f"字幕: {job.subtitle_path}")
                self.log.emit(f"输出: {job_output}")

                if job_output.exists() and not self.force:
                    self.log.emit("状态: 输出文件已存在，已跳过")
                    skipped += 1
                    continue

                def report_progress(
                    done_frames: int,
                    total_frames: int,
                    percent: float,
                    elapsed: float,
                ) -> None:
                    self.progress.emit(index, total, percent, elapsed)

                try:
                    burner = DanmakuBurner(
                        video_in=str(job.video_path),
                        xml_in=str(job.subtitle_path),
                        video_out=str(job_output),
                        encode_mode=self.encode_mode,
                        config=config,
                        force=self.force,
                        progress_callback=report_progress,
                        clip=clip,
                        drop_first=self.drop_first,
                    )
                    self.log.emit("状态: 开始压制")
                    burner.run()
                    elapsed = format_duration(perf_counter() - job_started_at)
                    self.log.emit(f"状态: 完成，用时 {elapsed}")
                    generated += 1
                except LiveDanmakuError as e:
                    elapsed = format_duration(perf_counter() - job_started_at)
                    self.log.emit(f"状态: 失败，用时 {elapsed}")
                    self.log.emit(f"原因 [{e.category.value}]: {e}")
                    failed += 1
                except Exception as e:
                    handle_error(e, component="gui", operation="batch_run")
                    elapsed = format_duration(perf_counter() - job_started_at)
                    self.log.emit(f"状态: 失败，用时 {elapsed}")
                    self.log.emit(f"原因: {e}")
                    failed += 1

            self.log.emit("")
            self.log.emit(
                "批量任务处理完成: "
                f"成功 {generated}, 跳过 {skipped}, 失败 {failed}, "
                f"总用时 {format_duration(perf_counter() - batch_started_at)}"
            )
        except Exception as e:
            self.log.emit(f"批量任务启动失败: {e}")
            failed = total - generated - skipped
        finally:
            logger.complete()
            sys.stdout = old_stdout
            sys.stderr = old_stderr
            self.summary.emit(total, generated, skipped, failed)



class DownloadWorker(QThread):
    """后台 yt-dlp 下载线程：进度行实时发到界面日志。

    request_stop() 可随时请求停止：已下载的部分保留在下载目录，
    结果经 stopped 信号通知界面（区别于失败/成功）。
    """

    log = Signal(str)
    succeeded = Signal(dict)
    failed = Signal(str)
    stopped = Signal(str)

    def __init__(
        self,
        url: str,
        fmt: str,
        out_dir,
        *,
        want_video: bool,
        want_chat: bool,
        want_thumbnail: bool,
        section: str | None = None,
        proxy: str = "",
    ):
        super().__init__()
        self.url = url
        self.fmt = fmt
        self.out_dir = Path(out_dir)
        self.section = section   # 界面传入的可能是字符串
        self.want_video = want_video
        self.want_chat = want_chat
        self.want_thumbnail = want_thumbnail
        self.proxy = proxy
        self._stop_requested = False

    def request_stop(self) -> None:
        """请求停止下载（非阻塞；由 run() 内的轮询检测并终止进程）。"""
        self._stop_requested = True

    def run(self) -> None:
        from ..core.pipeline import download_items
        from ..errors import LiveDanmakuError

        try:
            result = download_items(
                self.url, self.out_dir, self.fmt,
                want_video=self.want_video,
                want_chat=self.want_chat,
                want_thumbnail=self.want_thumbnail,
                on_output=self.log.emit,
                section=self.section,
                proxy=self.proxy,
                stop_flag=lambda: self._stop_requested,
            )
        except LiveDanmakuError as e:
            self.failed.emit(str(e))
        except Exception as e:
            self.failed.emit(f"下载失败: {e}")
        else:
            if result.get("stopped"):
                self.stopped.emit("下载已停止，已下载的部分保留在下载目录。")
            else:
                self.succeeded.emit(result)


class GifWorker(QThread):
    """后台视频转 GIF 线程：request_stop 可终止转换（半成品自动清理）。"""

    log = Signal(str)
    succeeded = Signal(dict)
    failed = Signal(str)
    stopped = Signal(str)

    def __init__(
        self,
        video: str,
        gif_out: str | None,
        *,
        start: float,
        end: float | None,
        speed: float,
        fps: int,
        resolution: str,
        quality: str,
        force: bool,
    ):
        super().__init__()
        self.video = video
        self.gif_out = gif_out
        self.start_time = start
        self.end = end
        self.speed = speed
        self.fps = fps
        self.resolution = resolution
        self.quality = quality
        self.force = force
        self._stop_requested = False

    def request_stop(self) -> None:
        """请求停止转换（非阻塞；由 make_gif 内的轮询检测）。"""
        self._stop_requested = True

    def run(self) -> None:
        from ..core.gifmaker import make_gif

        try:
            result = make_gif(
                self.video, self.gif_out,
                start=self.start_time, end=self.end,
                speed=self.speed, fps=self.fps,
                resolution=self.resolution, quality=self.quality,
                force=self.force,
                on_output=self.log.emit,
                stop_flag=lambda: self._stop_requested,
            )
        except LiveDanmakuError as e:
            self.failed.emit(str(e))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"GIF 转换失败: {e}")
        else:
            if result.get("stopped"):
                self.stopped.emit("GIF 转换已停止，半成品已清理。")
            else:
                self.succeeded.emit(result)


class RecordWorker(QThread):
    """后台直播录制线程。

    会话对象暴露为 self.session：界面线程可通过 stop_recording() /
    save_snapshot() 控制录制，QTimer 读取进度信息。
    """

    log = Signal(str)
    finished_rec = Signal(object)   # {returncode, files, chat}
    failed = Signal(str)

    def __init__(self, params):
        super().__init__()
        self.params = params
        self.session = None
        self._stop_requested = False

    def run(self) -> None:
        from ..core.recorder import RecordSession, backfill_chat_replay

        session = RecordSession(self.params)
        self.session = session
        try:
            try:
                session.start(on_output=self.log.emit)
                returncode = session.wait(stop_flag=lambda: self._stop_requested)

                # 整理中断产物：合并分离音视频、归位弹幕 JSON、清理期刊
                final = session.finalize()
                video = final.get("video")
                chat = final.get("chat")

                # 当前位置模式：完整录制了直播全程，裁掉起点之前的部分
                edge = getattr(session, "live_edge", None)
                if (
                    edge and video is not None and video.exists()
                    and not self.params.live_from_start
                ):
                    from ..core.recorder import probe_duration

                    duration = probe_duration(video)
                    if duration > edge + 30:
                        self.log.emit(
                            f"裁剪起点之前的内容（前 {edge:.0f} 秒）…"
                        )
                        trimmed = session._trim_video_start(video, edge)
                        if trimmed is not None:
                            video = trimmed
            except Exception as e:
                self.failed.emit(f"录制处理失败: {e}")
                return

            # 直播结束后 YouTube 才提供从头开始的弹幕回放；自动补全完整弹幕
            if self.params.record_chat and chat is not None:
                self.log.emit("正在获取从头开始的完整弹幕回放…（直播结束后可用）")
                try:
                    full = backfill_chat_replay(
                        self.params.url,
                        self.params.output_dir,
                        self.params.output_template,
                        proxy=self.params.proxy,
                        pot_token_script=self.params.pot_token_script,
                        current_size=chat.stat().st_size if chat.exists() else 0,
                        on_output=self.log.emit,
                    )
                except Exception as e:
                    self.log.emit(f"弹幕回放补全失败，保留实时弹幕: {e}")
                    full = None
                if full is not None:
                    chat = full
                    self.log.emit(f"已获取完整弹幕回放: {chat.name}")
                else:
                    self.log.emit("未获取到更完整的回放弹幕，保留录制时的实时弹幕。")

                # 当前位置模式：裁掉回放中早于录制起点的弹幕（与视频对齐）
                chat_edge = getattr(session, "live_edge", None)
                if (
                    not self.params.live_from_start
                    and chat_edge and chat is not None and chat.exists()
                ):
                    from ..input.chat2xml import filter_chat_json_by_time

                    # 弹幕时间轴（timestampUsec）是绝对 Unix 时间，
                    # 录制起点 = 会话启动的墙钟时间
                    min_unix = session.start_wall
                    kept = filter_chat_json_by_time(str(chat), min_unix)
                    self.log.emit(f"弹幕已对齐录制起点（保留 {kept} 条）")


            files = [p for p in (video, chat) if p is not None]
            self.finished_rec.emit({
                "returncode": returncode,
                "files": files,
                "chat": chat,
                "video": video,
            })
        except Exception as e:
            # 任何未预期异常都必须通知界面，否则按钮/状态会永久卡住
            self.failed.emit(f"录制处理失败: {e}")

    def stop_recording(self) -> None:
        """请求停止录制（非阻塞：发送停止信号，收尾在录制线程完成）。"""
        self._stop_requested = True

    def save_snapshot(self, target_dir=None) -> list:
        """保存当前已录制片段（不中断录制）。"""
        if self.session is None:
            return []
        return self.session.save_snapshot(target_dir)


class MonitorWorker(QThread):
    """后台频道开播监控线程：检测到开播时发 live_found 信号。"""

    log = Signal(str)
    live_found = Signal(str, str)   # (视频 URL, 标题)
    monitor_finished = Signal()
    failed = Signal(str)

    def __init__(self, channel_url: str, interval: float, proxy: str = "",
                 pot_token_script: str = ""):
        super().__init__()
        self.channel_url = channel_url
        self.interval = interval
        self.proxy = proxy
        self.pot_token_script = pot_token_script
        self._stop_flag = False

    def stop_monitor(self) -> None:
        self._stop_flag = True

    def run(self) -> None:
        from ..core.monitor import probe_channel_live

        self.log.emit(
            f"开始监控: {self.channel_url}（每 {self.interval:g} 秒探测一次）"
        )
        first = True
        while not self._stop_flag:
            try:
                url, title = probe_channel_live(
                    self.channel_url,
                    proxy=self.proxy,
                    pot_token_script=self.pot_token_script,
                )
            except Exception as e:
                self.log.emit(f"探测失败: {e}")
                url, title = None, ""
            if url is not None:
                self.log.emit(f"检测到开播: {title}")
                self.live_found.emit(url, title)
                break
            if first:
                self.log.emit("暂未开播，持续监控中…")
                first = False
            deadline = time.monotonic() + self.interval
            while not self._stop_flag and time.monotonic() < deadline:
                time.sleep(0.5)
        self.monitor_finished.emit()
