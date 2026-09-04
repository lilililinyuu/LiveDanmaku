"""FFmpeg 编码管理器

负责构建 FFmpeg 命令行、启动进程、写入帧数据、清理资源。
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import threading
from collections import deque

from loguru import logger

from ..config.models import DEFAULT_CONFIG, EncodeMode, EncodeParams, SystemParams
from ..errors import EncodeError
from ..layout.params import LayerParams

# Windows 的 GUI 进程没有可继承的控制台。显式禁止 FFmpeg/ffprobe
# 创建新控制台窗口；其他平台使用 0，保持 subprocess 的默认行为。
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class FFmpegManager:
    """FFmpeg 进程管理器"""

    def __init__(
        self,
        video_in: str,
        video_out: str,
        encode_mode: str = EncodeMode.AUTO,
        encode_params: EncodeParams = DEFAULT_CONFIG.encode,
        system_params: SystemParams = DEFAULT_CONFIG.system,
    ):
        self.video_in = video_in
        self.video_out = video_out
        self.encode_mode = encode_mode
        self.encode_params = encode_params
        self.system_params = system_params
        self.active_pipeline: str = EncodeMode.CPU
        self.process: subprocess.Popen | None = None
        self.stderr_thread: threading.Thread | None = None
        self.stderr_tail: deque[str] = deque(maxlen=20)

        self._resolve_encode_mode()

    def _resolve_encode_mode(self) -> None:
        """解析编码模式。"""
        if self.encode_mode == EncodeMode.CPU:
            self.active_pipeline = EncodeMode.CPU
            logger.info("编码模式: CPU (libx264)")
            return

        if self.encode_mode == EncodeMode.GPU:
            if not self._check_nvenc_available():
                raise RuntimeError("未检测到 NVENC 编码器")
            self.active_pipeline = EncodeMode.GPU
            logger.info("编码模式: GPU (NVENC)")
            return

        if self.encode_mode == EncodeMode.QSV:
            if not self._check_qsv_available():
                raise RuntimeError("未检测到 QSV 编码器")
            self.active_pipeline = EncodeMode.QSV
            logger.info("编码模式: QSV")
            return

        if self._check_nvenc_available():
            self.active_pipeline = EncodeMode.GPU
            logger.info("编码模式: GPU (NVENC) — 自动检测")
            return

        if self._check_qsv_available():
            self.active_pipeline = EncodeMode.QSV
            logger.info("编码模式: QSV — 自动回退")
            return

        self.active_pipeline = EncodeMode.CPU
        logger.info("编码模式: CPU (libx264) — 自动回退")

    @staticmethod
    def _check_nvenc_available() -> bool:
        """检查 NVENC 是否可用"""
        try:
            result = subprocess.run(
                ["ffmpeg", "-hide_banner", "-encoders"],
                capture_output=True, text=True, timeout=10,
                creationflags=_CREATE_NO_WINDOW,
            )
            if "h264_nvenc" not in result.stdout:
                return False
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-y", "-hide_banner",
                    "-f", "lavfi", "-i", "nullsrc=s=64x64:d=0.1",
                    "-c:v", "h264_nvenc", "-f", "null", "-",
                ],
                capture_output=True, text=True, timeout=15,
                creationflags=_CREATE_NO_WINDOW,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    @staticmethod
    def _check_qsv_available() -> bool:
        """检查 QSV 是否可用"""
        try:
            result = subprocess.run(
                ["ffmpeg", "-hide_banner", "-encoders"],
                capture_output=True, text=True, timeout=10,
                creationflags=_CREATE_NO_WINDOW,
            )
            if "h264_qsv" not in result.stdout:
                return False
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-y", "-hide_banner",
                    "-f", "lavfi", "-i", "nullsrc=s=64x64:d=0.1",
                    "-c:v", "h264_qsv", "-f", "null", "-",
                ],
                capture_output=True, text=True, timeout=15,
                creationflags=_CREATE_NO_WINDOW,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    @staticmethod
    def _parse_fraction(value: str | None) -> float:
        """解析 ffprobe 分数字段。"""
        if not value or value in {"0/0", "N/A"}:
            return 0.0
        try:
            if "/" not in value:
                return float(value)
            num, den = map(int, value.split("/", 1))
        except (TypeError, ValueError):
            return 0.0
        return num / den if den else 0.0

    @staticmethod
    def _parse_positive_int(value: object) -> int:
        """解析 ffprobe 可能返回的 nb_frames。"""
        if value in (None, "", "N/A"):
            return 0
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 0
        return max(0, parsed)

    @staticmethod
    def _parse_duration(*values: object) -> float:
        for value in values:
            if value in (None, "", "N/A"):
                continue
            try:
                duration = float(value)
            except (TypeError, ValueError):
                continue
            if duration > 0:
                return duration
        return 0.0

    def get_video_info(self) -> dict[str, int | float]:
        """获取视频元数据"""
        return FFmpegManager.probe_video(self.video_in)

    @staticmethod
    def probe_video(video_in: str) -> dict[str, int | float]:
        """用 ffprobe 读取视频元数据（宽度/高度/帧率/帧数）。"""
        cmd = [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries",
            "stream=width,height,avg_frame_rate,r_frame_rate,nb_frames,duration:format=duration",
            "-of", "json", video_in,
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                creationflags=_CREATE_NO_WINDOW,
            )
            data = json.loads(result.stdout)
            info = data["streams"][0]
        except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError, IndexError) as e:
            raise EncodeError(f"读取视频信息失败: {e}") from e

        fps = FFmpegManager._parse_fraction(info.get("avg_frame_rate"))
        if fps <= 0:
            fps = FFmpegManager._parse_fraction(info.get("r_frame_rate"))
        if fps <= 0:
            raise EncodeError("无法读取有效视频帧率")

        frames = FFmpegManager._parse_positive_int(info.get("nb_frames"))
        if frames == 0:
            duration = FFmpegManager._parse_duration(
                info.get("duration"),
                data.get("format", {}).get("duration"),
            )
            if duration <= 0:
                raise EncodeError("无法读取视频帧数或时长")
            frames = max(1, math.ceil(duration * fps))

        try:
            width = int(info["width"])
            height = int(info["height"])
        except (KeyError, TypeError, ValueError) as e:
            raise EncodeError(f"无法读取有效视频尺寸: {e}") from e

        return {"w": width, "h": height, "fps": fps, "frames": frames}

    def build_command(
        self,
        fps: float,
        w: int,
        h: int,
        layer_params: LayerParams,
        in_ss: float = 0.0,
        in_t: float | None = None,
    ) -> list[str]:
        """构建 FFmpeg 命令。

        Args:
            in_ss: 输入视频起始时间（秒），用于测试片段压制
            in_t: 输入视频编码时长（秒），None=到结尾
        """
        if self.active_pipeline == EncodeMode.GPU:
            return self._build_gpu_command(fps, w, h, layer_params, in_ss, in_t)
        if self.active_pipeline == EncodeMode.QSV:
            return self._build_qsv_command(fps, w, h, layer_params, in_ss, in_t)
        return self._build_cpu_command(fps, w, h, layer_params, in_ss, in_t)

    def _extra_args(self) -> list[str]:
        """用户自定义的 ffmpeg 参数（追加在输出文件之前）。"""
        return [str(arg) for arg in self.encode_params.extra_args]

    def _build_gpu_command(
        self, fps: float, w: int, h: int, lp: LayerParams,
        in_ss: float = 0.0, in_t: float | None = None,
    ) -> list[str]:
        cmd = ["ffmpeg", "-y"]
        if in_ss > 0:
            cmd += ["-ss", str(in_ss)]
        cmd += ["-i", self.video_in]
        return cmd + [
            "-f", "rawvideo",
            "-pix_fmt", "bgra",
            "-s", f"{lp.layer_w}x{lp.layer_h}",
            "-r", str(fps),
            "-i", "pipe:0",
        ] + (["-t", str(in_t)] if in_t is not None else []) + [
            "-filter_complex",
            (
                f"[0:v]scale={w}:{h}:flags=lanczos,format=yuv420p[bg];"
                f"[1:v]format=yuva420p[fg];"
                f"[bg][fg]overlay=x={lp.layer_x}:y={lp.layer_y},format=yuv420p[out]"
            ),
            "-map", "[out]",
            "-map", "0:a?",
            "-c:v", "h264_nvenc",
            "-preset", self.encode_params.gpu_preset,
            "-tune", "hq",
            "-profile:v", "high",
            "-pix_fmt", "yuv420p",
            "-rc:v", "constqp",
            "-qp", str(self.encode_params.gpu_cq),
            "-c:a", "copy",
            *self._extra_args(),
            self.video_out,
        ]

    def _build_qsv_command(
        self, fps: float, w: int, h: int, lp: LayerParams,
        in_ss: float = 0.0, in_t: float | None = None,
    ) -> list[str]:
        cmd = ["ffmpeg", "-y"]
        if in_ss > 0:
            cmd += ["-ss", str(in_ss)]
        cmd += ["-i", self.video_in]
        return cmd + [
            "-f", "rawvideo",
            "-pix_fmt", "bgra",
            "-s", f"{lp.layer_w}x{lp.layer_h}",
            "-r", str(fps),
            "-i", "pipe:0",
        ] + (["-t", str(in_t)] if in_t is not None else []) + [
            "-filter_complex",
            (
                f"[0:v]scale={w}:{h}:flags=lanczos,format=yuv420p[bg];"
                f"[1:v]format=yuva420p[fg];"
                f"[bg][fg]overlay=x={lp.layer_x}:y={lp.layer_y},format=nv12[out]"
            ),
            "-map", "[out]",
            "-map", "0:a?",
            "-c:v", "h264_qsv",
            "-preset", self.encode_params.qsv_preset,
            "-profile:v", "high",
            "-pix_fmt", "nv12",
            "-global_quality", str(self.encode_params.qsv_quality),
            "-c:a", "copy",
            *self._extra_args(),
            self.video_out,
        ]

    def _build_cpu_command(
        self, fps: float, w: int, h: int, lp: LayerParams,
        in_ss: float = 0.0, in_t: float | None = None,
    ) -> list[str]:
        cpu_count = os.cpu_count() or 4
        encode_threads = max(1, cpu_count - self.encode_params.cpu_min_reserve_threads)

        cmd = ["ffmpeg", "-y"]
        if in_ss > 0:
            cmd += ["-ss", str(in_ss)]
        cmd += ["-i", self.video_in]
        return cmd + [
            "-f", "rawvideo",
            "-pix_fmt", "bgra",
            "-s", f"{lp.layer_w}x{lp.layer_h}",
            "-r", str(fps),
            "-i", "pipe:0",
        ] + (["-t", str(in_t)] if in_t is not None else []) + [
            "-filter_complex",
            (
                f"[0:v]scale={w}:{h}:flags=lanczos[bg];"
                f"[1:v]format=yuva420p[fg];"
                f"[bg][fg]overlay=x={lp.layer_x}:y={lp.layer_y},format=yuv420p[out]"
            ),
            "-map", "[out]",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-profile:v", "high",
            "-pix_fmt", "yuv420p",
            "-preset", self.encode_params.cpu_preset,
            "-crf", str(self.encode_params.cpu_crf),
            "-threads", str(encode_threads),
            "-c:a", "copy",
            *self._extra_args(),
            self.video_out,
        ]

    def start(self, ffmpeg_cmd: list[str]) -> None:
        """启动 FFmpeg 进程"""
        logger.debug("FFmpeg 命令: {}", subprocess.list2cmdline(ffmpeg_cmd))
        self.process = subprocess.Popen(
            ffmpeg_cmd,
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=self.system_params.pipe_buffer_size,
            creationflags=_CREATE_NO_WINDOW,
        )

        def _read_stderr():
            assert self.process is not None
            assert self.process.stderr is not None
            for line in self.process.stderr:
                line_str = line.decode("utf-8", errors="replace").rstrip("\n\r")
                if line_str:
                    self.stderr_tail.append(line_str)
                    logger.debug(line_str)

        self.stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
        self.stderr_thread.start()
        logger.debug("FFmpeg 进程已启动")

    def _health_check(self) -> bool:
        """检查 FFmpeg 进程是否存活"""
        if self.process is None:
            return False
        if self.process.poll() is not None:
            logger.error(f"FFmpeg 异常退出 (code={self.process.returncode})")
            self._log_stderr_tail()
            return False
        return True

    def _log_stderr_tail(self) -> None:
        """输出最近几行 FFmpeg stderr，帮助定位失败原因。"""
        if not self.stderr_tail:
            return

        tail = "\n".join(self.stderr_tail)
        logger.error(f"FFmpeg 最近输出:\n{tail}")

    def submit_frame(self, data: memoryview) -> None:
        """提交一帧数据（同步写入 FFmpeg stdin）。

        Args:
            data: 帧像素数据的 memoryview 视图

        Raises:
            RuntimeError: FFmpeg 进程已死亡
            BrokenPipeError: FFmpeg 管道断开
        """
        if not self._health_check():
            raise RuntimeError("FFmpeg 进程已死亡")

        if self.process is None or self.process.stdin is None:
            raise RuntimeError("FFmpeg 未启动")

        try:
            self.process.stdin.write(data)
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise BrokenPipeError(f"FFmpeg 管道断开: {e}") from e

    def cleanup(self) -> None:
        """清理资源。

        按顺序完成：
        1. 关闭 stdin 管道，通知 FFmpeg 输入结束
        2. 等待 stderr 线程结束（避免日志丢失）
        3. 等待 FFmpeg 进程退出
        4. 关闭 stderr 管道，释放资源
        """
        proc = self.process
        if proc is None:
            return

        # 第一步：关闭 stdin，通知 FFmpeg 输入结束
        if proc.stdin:
            try:
                proc.stdin.close()
            except (OSError, BrokenPipeError):
                pass

        # 第二步：等待 FFmpeg 进程退出
        logger.debug("等待 FFmpeg 完成编码...")
        cleanup_error: EncodeError | None = None
        try:
            return_code = proc.wait(timeout=300.0)
            if return_code == 0:
                logger.success(f"压制完成: {self.video_out}")
            else:
                logger.error(f"压制失败 (code={return_code})")
                self._log_stderr_tail()
                cleanup_error = EncodeError(f"FFmpeg 编码失败，退出码 {return_code}")
        except subprocess.TimeoutExpired:
            logger.warning("FFmpeg 超时，强制终止")
            proc.kill()
            proc.wait()
            self._log_stderr_tail()
            cleanup_error = EncodeError("FFmpeg 编码超时，已强制终止")
        finally:
            # 第三步：等待 stderr 线程结束，确保日志不丢失
            if self.stderr_thread and self.stderr_thread.is_alive():
                self.stderr_thread.join(timeout=self.system_params.stderr_thread_timeout)

            # 第四步：关闭 stderr 管道，释放资源
            if proc.stderr:
                try:
                    proc.stderr.close()
                except (OSError, BrokenPipeError):
                    pass
            self.process = None

        if cleanup_error is not None:
            raise cleanup_error
