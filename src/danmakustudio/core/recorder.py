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

import os
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
    cmd = ["yt-dlp", "--newline", "--encoding", "utf-8"]
    if params.proxy.lower().startswith("socks"):
        logger.warning(
            "ffmpeg 不支持 SOCKS 代理，直播录制（HLS）大概率失败；"
            "建议把代理改为 http:// 形式（大多数代理客户端同端口支持 http）"
        )
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


def _replace_with_retry(src: Path, dst: Path, retries: int = 5, delay: float = 1.0) -> None:
    """os.replace，目标被占用（如 ffmpeg 尚未完全释放）时重试。"""
    for attempt in range(retries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == retries - 1:
                raise
            logger.warning(
                "目标被占用，{} 秒后重试（{}/{}）: {}",
                delay, attempt + 1, retries, dst,
            )
            time.sleep(delay)


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
            from .pipeline import _iter_output_lines

            for raw in _iter_output_lines(stream):
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

    def request_stop(self) -> None:
        """向全部录制进程发送 CTRL_BREAK（非阻塞，立即返回）。"""
        for proc in self.processes:
            if proc.poll() is not None:
                continue
            try:
                proc.send_signal(_CTRL_BREAK)
            except (OSError, ValueError):
                proc.terminate()

    def terminate_all(self) -> None:
        """强制终止全部录制进程（Windows 下用 taskkill /T 连子进程一起终止）。

        yt-dlp 录制直播时会派生自己的 ffmpeg 子进程；只 TerminateProcess
        父进程会留下仍在写 .part 的孤儿 ffmpeg，导致产物被占用无法整理。
        """
        for proc in self.processes:
            if proc.poll() is not None:
                continue
            logger.warning("录制进程未在限时内收尾，强制终止（含子进程）")
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True, check=False,
                    creationflags=_CREATE_NO_WINDOW,
                )
            else:
                proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    def wait(self, stop_flag: Callable[[], bool] | None = None) -> int | None:
        """等待全部录制进程结束，返回视频进程的退出码。

        stop_flag 返回 True 时请求停止：先发 CTRL_BREAK 优雅收尾，
        超过 30 秒仍未退出则强制终止。轮询不会阻塞界面线程。
        """
        if not self.processes:
            raise RuntimeError("录制尚未启动")
        stop_sent = False
        stop_sent_at = 0.0
        while True:
            alive = [p for p in self.processes if p.poll() is None]
            if not alive:
                break
            if stop_flag is not None and stop_flag():
                if not stop_sent:
                    self.request_stop()
                    stop_sent = True
                    stop_sent_at = time.time()
                    logger.info("已发送停止信号，等待 yt-dlp 收尾…")
                elif time.time() - stop_sent_at > 30.0:
                    self.terminate_all()
                    stop_sent_at = time.time()
            time.sleep(0.2)
        returncode: int | None = None
        for index, proc in enumerate(self.processes):
            rc = proc.wait()
            if index == 0:
                returncode = rc
        return returncode

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

    def chat_file(self) -> Path | None:
        """返回当前会话的弹幕 JSON 文件（可能仍是 .part 形态）。"""
        for src in self.current_files():
            if ".live_chat.json" in src.name.lower():
                return src
        return None

    def finalize(self) -> dict[str, Path | None]:
        """录制结束后整理产物。

        处理被强制终止时遗留的写入中文件：
        - 成对的视频/音频 .fNNN.mp4(.part) 用 ffmpeg -c copy 合并为
          <base>.mp4（带音轨）；
        - 弹幕 xxx.live_chat.json(.part) 改名为 xxx.live_chat.json；
        - .ytdl 期刊删除。

        返回 {"video": 成品视频路径或 None, "chat": 弹幕路径或 None}。
        """
        video_out: Path | None = None
        chat_out: Path | None = None
        media: list[Path] = []
        # .ytdl 期刊被 current_files 过滤，需单独清理
        for journal in self.params.output_dir.glob("*.ytdl"):
            journal.unlink(missing_ok=True)
        for src in self.current_files():
            name = src.name
            lower = name.lower()
            if lower.endswith(".ytdl"):
                src.unlink(missing_ok=True)
                continue
            if ".live_chat.json" in lower:
                if lower.endswith(".part"):
                    renamed = src.with_name(name[: -len(".part")])
                    _replace_with_retry(src, renamed)
                    chat_out = renamed
                else:
                    chat_out = src
                continue
            clean = src.with_name(re.sub(r"\.part$", "", name))
            if clean != src:
                _replace_with_retry(src, clean)
            if re.search(r"\.f\d+\.(mp4|m4a)$", clean.name, re.IGNORECASE):
                media.append(clean)
            elif video_out is None:
                video_out = clean   # 单文件格式（如 best）直接是成品
        if len(media) >= 2:
            merged = self._merge_media(media, self.params.output_dir)
            if merged is not None:
                for m in media:
                    m.unlink(missing_ok=True)
                video_out = merged
            else:
                video_out = media[0]
        elif media:
            video_out = media[0]
        return {"video": video_out, "chat": chat_out}

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

def backfill_chat_replay(
    url: str,
    output_dir: Path,
    template: str,
    *,
    proxy: str = "",
    pot_token_script: str = "",
    current_size: int = 0,
    on_output: Callable[[str], None] | None = None,
    timeout: float = 900.0,
) -> Path | None:
    """直播结束后补全从头开始的完整弹幕回放。

    YouTube 仅在直播结束后提供整场弹幕回放。把完整 live_chat.json 下载到
    输出目录的「弹幕回放」子目录，仅当其比实时捕获的部分（current_size）
    更大时才替换并返回路径；否则返回 None（保留现有文件）。
    """
    replay_dir = output_dir / "弹幕回放"
    replay_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["yt-dlp", "--newline", "--encoding", "utf-8",
           "--skip-download", "--write-subs", "--sub-langs", "live_chat"]
    if proxy:
        cmd += ["--proxy", proxy]
    if pot_token_script:
        cmd += [
            "--extractor-args",
            f"youtubepot-bgutilscript:script_path={pot_token_script}",
        ]
    cmd += ["-o", str(replay_dir / template), url]

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_CREATE_NO_WINDOW,
    )
    stdout_pipe = process.stdout
    stderr_pipe = process.stderr
    assert stdout_pipe is not None and stderr_pipe is not None

    def _drain(stream, pref):
        from .pipeline import _iter_output_lines

        for raw in _iter_output_lines(stream):
            line_str = raw.strip()
            if line_str and on_output is not None:
                on_output(pref + line_str)

    threading.Thread(target=_drain, args=(stdout_pipe, ""), daemon=True).start()
    threading.Thread(
        target=_drain, args=(stderr_pipe, "[yt-dlp] "), daemon=True
    ).start()
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        logger.warning("弹幕回放下载超时（{} 秒），保留实时弹幕", timeout)
        process.kill()
        return None
    if returncode != 0:
        logger.warning("弹幕回放下载失败（退出码 {}）", returncode)
        return None

    replays = [
        f for f in replay_dir.iterdir()
        if f.is_file() and ".live_chat.json" in f.name.lower()
    ]
    if not replays:
        logger.warning("回放目录中未找到弹幕 JSON")
        return None
    best = max(replays, key=lambda f: f.stat().st_size)
    if best.stat().st_size <= current_size:
        logger.info("回放弹幕不比实时捕获的部分大，保留实时弹幕")
        shutil.rmtree(replay_dir, ignore_errors=True)
        return None
    target = output_dir / best.name
    os.replace(best, target)
    shutil.rmtree(replay_dir, ignore_errors=True)
    logger.info("已用完整回放弹幕替换: {}", target)
    return target
