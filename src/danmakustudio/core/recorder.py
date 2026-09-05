"""实时录制直播。

基于 yt-dlp 的直播录制封装：
- 录制进行中的直播（可选 --live-from-start 从头录制）；
- 同步录制弹幕 JSON：视频与弹幕分属两个 yt-dlp 进程（yt-dlp 的
  live-from-start 模式下弹幕抓取与视频下载无法同进程并行，实测只会
  产出弹幕）；
- 输出文件跟踪与「保存当前片段」（复制正在写入的文件，不打断录制；
  分离音视频快照用 ffmpeg -c copy 合并为单个 mp4）；
- 优雅停止（CTRL_BREAK -> yt-dlp 收尾；超时强制终止兜底）。
"""

from __future__ import annotations

import re
import shutil
import signal
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

# Windows 的 GUI 进程没有可继承的控制台；录制进程需要独立进程组
# 以便发送 CTRL_BREAK 优雅停止。其他平台使用 0。
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
_CTRL_BREAK = getattr(signal, "CTRL_BREAK_EVENT", signal.SIGINT)


@dataclass
class RecordParams:
    """一次直播录制的全部参数。"""

    url: str
    output_dir: Path
    output_template: str = "%(upload_date)s_%(title)s.%(ext)s"
    fmt: str = "bestvideo+bestaudio/best"   # 优先双流+合并，无双流时回退单文件
    live_from_start: bool = True            # 从直播开头录制（不支持时 yt-dlp 自动忽略）
    proxy: str = ""                         # 空 = 不使用代理
    pot_token_script: str = ""              # bgutil-pot 路径，空 = 不传
    record_chat: bool = True                # 同步录制弹幕 JSON（独立进程）
    extra_args: list[str] = field(default_factory=list)


def build_record_command(
    params: RecordParams, *, chat_only: bool = False
) -> list[str]:
    """拼装 yt-dlp 命令。

    chat_only=True 时生成仅抓取弹幕的命令（--skip-download +
    --write-subs live_chat）；否则生成视频录制命令（不带弹幕抓取）。
    """
    cmd = ["yt-dlp", "--newline"]
    if params.live_from_start:
        cmd.append("--live-from-start")
    if params.proxy:
        cmd += ["--proxy", params.proxy]
    if params.pot_token_script:
        cmd += [
            "--extractor-args",
            f"youtubepot-bgutilscript:script_path={params.pot_token_script}",
        ]
    if chat_only:
        cmd += ["--skip-download", "--write-subs", "--sub-langs", "live_chat"]
    else:
        cmd += ["-f", params.fmt]
        if "+" in params.fmt:
            cmd += ["--merge-output-format", "mp4"]
    cmd += ["-o", str(params.output_dir / params.output_template)]
    cmd += [str(arg) for arg in params.extra_args]
    cmd.append(params.url)
    return cmd


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def normalize_stream_url(url: str) -> str:
    """规整输入 URL：频道主页自动指向 /live 探测页；其余原样使用。"""
    base = url.strip().rstrip("/")
    if base.endswith("/live") or "watch?v=" in base or "youtu.be/" in base:
        return base
    return base + "/live"


class RecordSession:
    """一次录制会话：进程生命周期、输出跟踪、停止与片段保存。"""

    def __init__(self, params: RecordParams):
        self.params = params
        self.processes: list[subprocess.Popen] = []
        self.stderr_tail: deque[str] = deque(maxlen=30)
        self._before: set[Path] = set()
        self._started_at: float | None = None

    def start(self, on_output: Callable[[str], None] | None = None) -> None:
        """启动录制进程（视频 + 可选弹幕）。重复调用抛 RuntimeError。"""
        if self.processes:
            raise RuntimeError("录制已在进行中")
        self.params.output_dir.mkdir(parents=True, exist_ok=True)
        self._before = {p for p in self.params.output_dir.iterdir() if p.is_file()}
        self._started_at = time.time()

        # 视频录制进程（弹幕与视频同进程互斥，拆分为两个进程并行）
        self.processes.append(
            self._spawn(build_record_command(self.params), on_output, "")
        )
        if self.params.record_chat:
            chat_cmd = build_record_command(self.params, chat_only=True)
            self.processes.append(self._spawn(chat_cmd, on_output, "[弹幕] "))
        logger.info("已启动 {} 个 yt-dlp 录制进程", len(self.processes))

    def _spawn(
        self,
        cmd: list[str],
        on_output: Callable[[str], None] | None,
        prefix: str,
    ) -> subprocess.Popen:
        logger.info("启动 yt-dlp: {}", " ".join(cmd))
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP,
        )
        stdout_pipe = proc.stdout
        stderr_pipe = proc.stderr
        assert stdout_pipe is not None and stderr_pipe is not None

        def _drain(stream, pref: str) -> None:
            for raw in stream:
                line_str = raw.strip()
                if line_str and on_output is not None:
                    on_output(f"{pref}{line_str}")

        threading.Thread(
            target=_drain, args=(stdout_pipe, prefix), daemon=True
        ).start()
        threading.Thread(
            target=_drain, args=(stderr_pipe, "[yt-dlp] "), daemon=True
        ).start()
        return proc

    def wait(self, timeout: float | None = None) -> int | None:
        """等待全部录制进程结束，返回视频进程的退出码。"""
        if not self.processes:
            raise RuntimeError("录制尚未启动")
        returncode: int | None = None
        for index, proc in enumerate(self.processes):
            rc = proc.wait(timeout=timeout)
            if index == 0:
                returncode = rc
        return returncode

    def stop(self, timeout: float = 30.0) -> None:
        """优雅停止录制：先 CTRL_BREAK 让 yt-dlp 收尾，超时强制终止。

        可从任意线程调用（发送的是 OS 级信号，不触碰 Qt）。
        """
        deadline = time.time() + timeout
        for proc in self.processes:
            if proc.poll() is not None:
                continue
            try:
                proc.send_signal(_CTRL_BREAK)
            except (OSError, ValueError):
                proc.terminate()
        for proc in self.processes:
            if proc.poll() is not None:
                continue
            try:
                proc.wait(timeout=max(1.0, deadline - time.time()))
            except subprocess.TimeoutExpired:
                logger.warning("录制进程未在 {} 秒内收尾，强制终止", timeout)
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()

    def current_files(self) -> list[Path]:
        """录制开始后新产生的输出文件（按修改时间从新到旧）。

        已排除临时与期刊文件（.tmp/.ytdl/分片临时 .part-FragNN）。
        """
        try:
            after = {
                p for p in self.params.output_dir.iterdir()
                if p.is_file() and not p.name.endswith(".tmp")
            }
        except OSError as e:
            logger.warning("无法扫描录制目录: {} ({})", self.params.output_dir, e)
            return []
        new_files = sorted(
            after - self._before,
            key=_safe_mtime,
            reverse=True,
        )
        kept = []
        for p in new_files:
            lower = p.name.lower()
            if lower.endswith(".ytdl") or ".part-frag" in lower:
                continue
            kept.append(p)
        return kept

    def save_snapshot(self, target_dir: Path | None = None) -> list[Path]:
        """把当前已录制的部分复制到目标目录（不中断录制）。

        分离音视频的快照会用 ffmpeg -c copy 合并为单个 mp4。
        返回复制出的文件路径列表；尚无输出文件时返回空列表。
        """
        target = target_dir or (self.params.output_dir / "已保存")
        target.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        copied: list[Path] = []
        media: list[Path] = []
        for src in self.current_files():
            # 剥离 .part / .part-FragNN / .part-FragNN.part 等写入中后缀
            name = re.sub(r"\.part(-Frag\d+)?(\.part)?$", "", src.name)
            dst = target / f"{stamp}_{name}"
            shutil.copyfile(src, dst)
            copied.append(dst)
            logger.info("已保存录制片段: {}", dst)
            if re.search(r"\.f\d+\.(mp4|m4a)$", dst.name, re.IGNORECASE):
                media.append(dst)
        if len(media) >= 2:
            merged = self._merge_media(media, target)
            if merged is not None:
                for m in media:
                    m.unlink(missing_ok=True)
                copied = [f for f in copied if f not in media] + [merged]
        return copied

    def _merge_media(self, media: list[Path], target: Path) -> Path | None:
        """把分离的音视频快照用 ffmpeg -c copy 合并为单个 mp4（秒级完成）。

        成功时返回合并文件；失败（如无 ffmpeg）时返回 None，保留原始片段。
        ffmpeg 默认跨输入选择最优视频/音频流，无需区分谁是视频谁是音频。
        """
        base = re.sub(r"\.f\d+$", "", Path(media[0]).stem)
        out = target / f"{base}.mp4"
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
        for m in media:
            cmd += ["-i", str(m)]
        cmd += ["-c", "copy", "-movflags", "+faststart", str(out)]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, check=False,
                creationflags=_CREATE_NO_WINDOW,
            )
        except OSError as e:
            logger.warning("片段合并失败（找不到 ffmpeg？）: {}", e)
            return None
        if result.returncode != 0 or not out.exists() or out.stat().st_size == 0:
            logger.warning("片段合并失败: {}", result.stderr[-200:])
            return None
        logger.info("片段已合并音视频: {}", out)
        return out

    @property
    def elapsed_seconds(self) -> float:
        if self._started_at is None:
            return 0.0
        return max(0.0, time.time() - self._started_at)

    @property
    def total_size(self) -> int:
        return sum(_safe_size(p) for p in self.current_files())
