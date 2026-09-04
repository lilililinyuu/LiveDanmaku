"""GUI 后台批量压制线程。"""

from __future__ import annotations

import re
import sys
from dataclasses import replace
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
            if self.test_clip:
                self.log.emit(f"测试片段模式: {self.clip_duration} 秒")
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
                    clip = (None, self.clip_duration)
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

