"""GUI 后台批量压制线程。"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import replace
from pathlib import Path
from time import perf_counter

from loguru import logger
from PySide6.QtCore import QThread, Signal

from ..batch import BatchJob
from ..config.loader import load_config
from ..config.models import EncodeMode, StyleOverrides, apply_style_overrides
from ..core.burner import DanmakuBurner
from ..errors import DanmakuStudioError, handle_error
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
            if cleaned:
                self.log_signal.emit(cleaned)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False


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
        ffmpeg_args: str | None = None,
        test_clip: bool = False,
        clip_duration: float = 60.0,
        drop_first: int = 0,
        clip_start: float | None = None,
    ):
        super().__init__()
        self.jobs = jobs
        self.encode_mode = encode_mode
        self.config_path = config_path
        self.force = force
        self.style_overrides = style_overrides
        self.first_danmaku_at = first_danmaku_at
        self.ffmpeg_args = ffmpeg_args
        self.test_clip = test_clip
        self.clip_duration = clip_duration
        self.drop_first = drop_first
        self.clip_start = clip_start

    def run(self) -> None:
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        stream = QtLogStream(self.log)
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
            if self.first_danmaku_at is not None:
                config = replace(
                    config,
                    animation=replace(
                        config.animation, first_danmaku_at=self.first_danmaku_at
                    ),
                )
                self.log.emit(f"首条弹幕时间: {self.first_danmaku_at} 秒")
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
                except DanmakuStudioError as e:
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
    """后台 yt-dlp 下载线程：进度行实时发到界面日志。"""

    log = Signal(str)
    succeeded = Signal(dict)
    failed = Signal(str)

    def __init__(
        self,
        url: str,
        fmt: str,
        out_dir,
        *,
        want_video: bool,
        want_chat: bool,
        want_thumbnail: bool,
    ):
        super().__init__()
        self.url = url
        self.fmt = fmt
        self.out_dir = Path(out_dir)   # 界面传入的可能是字符串
        self.want_video = want_video
        self.want_chat = want_chat
        self.want_thumbnail = want_thumbnail

    def run(self) -> None:
        from ..core.pipeline import download_items
        from ..errors import DanmakuStudioError

        try:
            result = download_items(
                self.url, self.out_dir, self.fmt,
                want_video=self.want_video,
                want_chat=self.want_chat,
                want_thumbnail=self.want_thumbnail,
                on_output=self.log.emit,
            )
        except DanmakuStudioError as e:
            self.failed.emit(str(e))
        except Exception as e:
            self.failed.emit(f"下载失败: {e}")
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

    def run(self) -> None:
        from ..core.recorder import RecordSession

        session = RecordSession(self.params)
        self.session = session
        try:
            session.start(on_output=self.log.emit)
            returncode = session.wait()
        except Exception as e:
            self.failed.emit(f"录制启动失败: {e}")
            return

        files = session.current_files()
        chat = next(
            (f for f in files if f.name.endswith(".live_chat.json")), None
        )
        self.finished_rec.emit({
            "returncode": returncode,
            "files": files,
            "chat": chat,
        })

    def stop_recording(self, timeout: float = 30.0) -> None:
        """优雅停止录制（OS 级信号，可从界面线程调用）。"""
        if self.session is not None:
            self.session.stop(timeout=timeout)

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
