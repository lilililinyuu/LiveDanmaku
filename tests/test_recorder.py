"""直播录制模块单元测试。"""

import signal
import subprocess
from pathlib import Path

import pytest

from danmakustudio.config.models import RecorderParams
from danmakustudio.core.recorder import (
    RecordParams,
    RecordSession,
    build_record_command,
)

_CTRL_BREAK = getattr(signal, "CTRL_BREAK_EVENT", signal.SIGINT)


def _params(tmp_path: Path, **overrides) -> RecordParams:
    defaults = {
        "url": "https://www.youtube.com/watch?v=abc",
        "output_dir": tmp_path / "rec",
        "fmt": "bestvideo+bestaudio/best",
        "live_from_start": True,
        "proxy": "socks5://127.0.0.1:7897",
        "pot_token_script": r"D:\bgutil\bgutil-pot.exe",
        "record_chat": True,
    }
    defaults.update(overrides)
    return RecordParams(**defaults)


class _FakeStream:
    """供 _iter_output_lines 读取的伪流：read() 一次性返回全部文本。"""

    def __init__(self, text=""):
        self._text = text

    def read(self, n=-1):
        t, self._text = self._text, ""
        return t


class _FakePopen:
    """模拟录制进程：stdout 输出若干行后结束。"""

    def __init__(self, lines, output_dir: Path | None = None):
        self.args = []
        self.stdout = _FakeStream("\n".join(lines) + ("\n" if lines else ""))
        self.stderr = _FakeStream()
        self.returncode = None
        self.signals = []

    def send_signal(self, sig):
        self.signals.append(sig)
        self.returncode = 0

    def terminate(self):
        self.signals.append("terminate")
        self.returncode = 0

    def kill(self):
        self.signals.append("kill")

    def poll(self):
        # 模拟进程行为：输出结束后自然退出
        self._polls = getattr(self, "_polls", 0) + 1
        if self.returncode is None and self._polls > 3:
            self.returncode = 0
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def communicate(self, *args, **kwargs):
        self.returncode = 0
        return (b"", b"")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.returncode = 0
        return False


def test_build_record_command_full(tmp_path):
    """全部选项开启时的命令拼装。"""
    cmd = build_record_command(_params(tmp_path))

    assert cmd[0] == "yt-dlp" and "--newline" in cmd
    assert "--live-from-start" in cmd
    assert "--proxy" in cmd and "socks5://127.0.0.1:7897" in cmd
    assert "--extractor-args" in cmd
    assert any(
        "youtubepot-bgutilscript:script_path=D:\\bgutil\\bgutil-pot.exe" in a
        for a in cmd
    )
    assert "-f" in cmd and "bestvideo+bestaudio/best" in cmd
    # 双流格式自动追加合并容器
    assert "--merge-output-format" in cmd and "mp4" in cmd
    # 弹幕抓取拆分为独立进程，视频命令不含 --write-subs
    assert "--write-subs" not in cmd and "--sub-langs" not in cmd
    assert "-o" in cmd
    assert cmd[-1] == "https://www.youtube.com/watch?v=abc"
    # -o 的值紧跟其后，指向输出目录
    assert str(tmp_path / "rec") in cmd[cmd.index("-o") + 1]


def test_build_record_command_minimal(tmp_path):
    """可选项目关闭时对应参数不应出现。

    live_from_start=False 且未探测到边缘时回退为从直播开头
    （保证音画同步），故 --live-from-start 仍在。
    """
    cmd = build_record_command(
        _params(
            tmp_path,
            live_from_start=False,
            proxy="",
            pot_token_script="",
            record_chat=False,
            fmt="best",
        ),
        live_edge=None,
    )
    assert "--live-from-start" in cmd
    assert "--download-sections" not in cmd
    assert "--proxy" not in cmd
    assert "--extractor-args" not in cmd
    assert "--write-subs" not in cmd
    # 单文件格式无需合并容器
    assert "--merge-output-format" not in cmd


def test_build_record_command_current_position(tmp_path):
    """当前位置模式：live-from-start + download-sections 从边缘起录，
    且视频与弹幕命令使用同一起点（音画对齐）。"""
    params = _params(tmp_path, live_from_start=False, record_chat=True)
    video_cmd = build_record_command(params, live_edge=5144.0)
    chat_cmd = build_record_command(params, chat_only=True, live_edge=5144.0)

    for cmd in (video_cmd, chat_cmd):
        assert "--live-from-start" in cmd
        idx = cmd.index("--download-sections")
        assert cmd[idx + 1] == "*5146-inf"   # 边缘 +2s 余量

    # 弹幕命令保留抓取参数，视频命令不含
    assert "--write-subs" in chat_cmd and "--write-subs" not in video_cmd


def test_build_chat_command(tmp_path):
    """弹幕进程命令：--skip-download + live_chat 抓取，不含视频格式。"""
    cmd = build_record_command(_params(tmp_path), chat_only=True)
    assert "--skip-download" in cmd
    assert "--write-subs" in cmd and "--sub-langs" in cmd and "live_chat" in cmd
    assert "-f" not in cmd
    assert "--live-from-start" in cmd and "--proxy" in cmd


def test_record_session_streams_output_and_tracks_files(tmp_path, monkeypatch):
    """录制进程的输出行应回调给 on_output，新文件应被跟踪。"""
    params = _params(tmp_path)
    session = RecordSession(params)
    received = []

    def fake_popen(cmd, **kwargs):
        out = params.output_dir / "20260905_测试直播.mp4"
        out.write_bytes(b"x" * 128)
        return _FakePopen([
            "[download] 已开始录制",
            "[download] 10.0 MiB",
        ])

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    session.start(on_output=received.append)
    session.wait(stop_flag=lambda: True)   # 立即请求停止以结束轮询

    assert any("已开始录制" in line for line in received)
    files = session.current_files()
    assert len(files) == 1 and files[0].name == "20260905_测试直播.mp4"
    assert session.total_size == 128
    assert session.elapsed_seconds >= 0


def test_record_session_stop_sends_ctrl_break(tmp_path, monkeypatch):
    """停止录制应对全部录制进程发送 CTRL_BREAK 优雅收尾。"""
    params = _params(tmp_path)
    session = RecordSession(params)

    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: _FakePopen([]))
    session.start()
    session.wait(stop_flag=lambda: True)   # 立即请求停止

    assert all(p.signals[0] == _CTRL_BREAK for p in session.processes)


def test_record_session_save_snapshot(tmp_path, monkeypatch):
    """保存片段：复制输出文件并剥离写入中后缀；分片临时文件排除。"""
    params = _params(tmp_path)
    session = RecordSession(params)

    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: _FakePopen([]))
    session.start()
    (params.output_dir / "20260905_测试直播.mp4.part").write_bytes(b"x" * 64)
    (params.output_dir / "20260905_测试直播.live_chat.json").write_bytes(b"{}")
    (params.output_dir / "20260905_测试直播.mp4.part-Frag19.part").write_bytes(b"junk")

    copied = session.save_snapshot()

    # 分片临时文件（.part-Frag19）被排除；其余文件剥离 .part 后缀
    assert len(copied) == 2
    assert all("已保存" in str(f.parent) for f in copied)
    assert all(
        not f.name.endswith(".part") and ".part-Frag" not in f.name for f in copied
    )
    assert {f.stat().st_size for f in copied} == {64, 2}


def test_record_session_double_start_raises(tmp_path, monkeypatch):
    """重复启动应抛出 RuntimeError。"""
    session = RecordSession(_params(tmp_path))
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: _FakePopen([]))

    session.start()
    with pytest.raises(RuntimeError, match="已在进行中"):
        session.start()


def test_recorder_params_defaults():
    params = RecorderParams()
    assert params.fmt == "bestvideo+bestaudio/best"
    assert params.record_chat is True
    assert params.live_from_start is True
    assert params.monitor_interval == 60.0
    with pytest.raises(ValueError, match="monitor_interval"):
        RecorderParams(monitor_interval=0)


def test_snapshot_merge_fallback_keeps_raw_copies(tmp_path, monkeypatch):
    """ffmpeg 合并失败（假媒体文件）时保留原始视频/音频片段。"""
    params = _params(tmp_path)
    session = RecordSession(params)

    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: _FakePopen([]))
    session.start()
    video = params.output_dir / "live.f137.mp4"
    audio = params.output_dir / "live.f140.m4a"
    video.write_bytes(b"fake-video")
    audio.write_bytes(b"fake-audio")

    copied = session.save_snapshot()

    names = [f.name for f in copied]
    assert any(n.endswith(".mp4") for n in names)
    assert any(n.endswith(".m4a") for n in names)


def test_snapshot_merge_success_real_ffmpeg(tmp_path, monkeypatch):
    """有真实 ffmpeg 时，分离音视频快照应合并为单个带音轨的 mp4。"""
    import shutil as _shutil

    if _shutil.which("ffmpeg") is None:
        pytest.skip("需要 ffmpeg")

    params = _params(tmp_path)
    session = RecordSession(params)
    params.output_dir.mkdir(parents=True, exist_ok=True)

    flags = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    # 会话先启动（快照为空），媒体文件随后"写盘"——与真实录制时序一致
    with monkeypatch.context() as m:
        m.setattr(subprocess, "Popen", lambda cmd, **kw: _FakePopen([]))
        session.start()

    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "testsrc=duration=0.5", "-pix_fmt", "yuv420p",
         str(params.output_dir / "live.f137.mp4")],
        check=True, **flags,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "sine=duration=0.5",
         str(params.output_dir / "live.f140.m4a")],
        check=True, **flags,
    )

    copied = session.save_snapshot()

    # 原始两个片段被合并清理，只剩带音轨的 mp4
    assert len(copied) == 1
    assert copied[0].name.endswith("live.mp4")
    assert copied[0].stat().st_size > 0


def test_finalize_merges_and_renames(tmp_path, monkeypatch):
    """finalize 应合并双流 .part、归位弹幕 JSON、清理期刊文件。"""
    import shutil as _shutil

    if _shutil.which("ffmpeg") is None:
        pytest.skip("需要 ffmpeg")

    params = _params(tmp_path)
    session = RecordSession(params)

    flags = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    staging = tmp_path / "staging"
    staging.mkdir()
    # ffmpeg 无法推断 .part 扩展名的输出封装器，先生成到暂存目录再改名
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "testsrc=duration=0.5", "-pix_fmt", "yuv420p",
         str(staging / "v.mp4")],
        check=True, **flags,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "sine=duration=0.5", str(staging / "a.m4a")],
        check=True, **flags,
    )

    # 会话启动需要假 Popen（context 限定，finalize 的合并用真实 ffmpeg）
    with monkeypatch.context() as m:
        m.setattr(subprocess, "Popen", lambda cmd, **kw: _FakePopen([]))
        session.start()   # 记录 _before（此时输出目录为空）

    # 模拟录制中写盘：媒体以 .part 形态出现在输出目录
    _shutil.copyfile(staging / "v.mp4", params.output_dir / "直播.f299.mp4.part")
    _shutil.copyfile(staging / "a.m4a", params.output_dir / "直播.f140.m4a.part")
    chat = params.output_dir / "直播.live_chat.json.part"
    chat.write_text('{"replay": true}', encoding="utf-8")
    (params.output_dir / "直播.ytdl").write_bytes(b"")

    result = session.finalize()

    # 双流合并为带音轨的 mp4；弹幕归位；期刊删除
    assert result["video"] is not None and result["video"].name == "直播.mp4"
    assert result["video"].stat().st_size > 0
    assert result["chat"] is not None and result["chat"].name == "直播.live_chat.json"
    assert not (params.output_dir / "直播.f299.mp4.part").exists()
    assert not (params.output_dir / "直播.f140.m4a.part").exists()
    assert not (params.output_dir / "直播.ytdl").exists()


def test_finalize_without_merge(tmp_path, monkeypatch):
    """合并失败（假媒体）时保留剥离 .part 后的单流文件与弹幕。"""
    params = _params(tmp_path)
    session = RecordSession(params)
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: _FakePopen([]))
    session.start()
    (params.output_dir / "直播.f299.mp4.part").write_bytes(b"fake")
    (params.output_dir / "直播.live_chat.json.part").write_text("{}", encoding="utf-8")

    result = session.finalize()

    assert result["video"] is not None and result["video"].name == "直播.f299.mp4"
    assert result["chat"] is not None and result["chat"].name == "直播.live_chat.json"


def test_build_record_command_warns_on_socks(tmp_path):
    """socks 代理时命令照常拼装（由录制页默认值避免该情况）。"""
    cmd = build_record_command(_params(tmp_path, proxy="socks5://127.0.0.1:7897"))

    assert "--proxy" in cmd and "socks5://127.0.0.1:7897" in cmd


def test_finalize_remuxes_live_ts_to_mp4(tmp_path, monkeypatch):
    """回归：直播 HLS 的 .part 实为 mpegts，finalize 应重封装为规范 mp4
    （此前直接改名导致音画不同步、进度条异常）。"""
    import shutil as _shutil

    if _shutil.which("ffmpeg") is None:
        pytest.skip("需要 ffmpeg")

    params = _params(tmp_path)
    session = RecordSession(params)

    flags = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    staging = tmp_path / "staging"
    staging.mkdir()
    # 生成真实的 mpegts（含音视频，与直播 .part 相同的容器）
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=duration=0.5",
         "-f", "lavfi", "-i", "sine=duration=0.5",
         "-pix_fmt", "yuv420p", "-c:v", "libx264", "-c:a", "aac",
         "-f", "mpegts", str(staging / "v.ts")],
        check=True, **flags,
    )

    with monkeypatch.context() as m:
        m.setattr(subprocess, "Popen", lambda cmd, **kw: _FakePopen([]))
        session.start()   # 记录 _before（此时输出目录为空）

    _shutil.copyfile(staging / "v.ts", params.output_dir / "直播.mp4.part")
    (params.output_dir / "直播.live_chat.json.part").write_text("{}", encoding="utf-8")

    result = session.finalize()

    video = result["video"]
    assert video is not None and video.name == "直播.mp4"
    # mp4 容器特征：偏移 4 字节起为 "ftyp"（mpegts 则以 0x47 同步字节开头）
    data = video.read_bytes()
    assert data[4:8] == b"ftyp"
    # 弹幕 .part 已归位
    assert result["chat"] is not None and result["chat"].name == "直播.live_chat.json"


def test_remux_exact_mode_reencodes(tmp_path):
    """exact 模式应走重编码路径并产出规范 mp4。"""
    import shutil as _shutil

    if _shutil.which("ffmpeg") is None:
        pytest.skip("需要 ffmpeg")

    params = _params(tmp_path, sync_mode="exact")
    session = RecordSession(params)

    flags = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    params.output_dir.mkdir(parents=True, exist_ok=True)
    # ffmpeg 对非 ASCII 输出路径兼容性差，源文件用 ASCII 名生成
    src = params.output_dir / "live.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=duration=0.5",
         "-f", "lavfi", "-i", "sine=duration=0.5",
         "-pix_fmt", "yuv420p", "-c:v", "libx264", "-c:a", "aac",
         "-f", "mpegts", str(src)],
        check=True, **flags,
    )

    remuxed = session._remux_to_mp4(src)

    assert remuxed is not None and remuxed == src
    data = src.read_bytes()
    assert data[4:8] == b"ftyp"


def test_recorder_params_sync_mode_default():
    assert RecorderParams().sync_mode == "auto"
    with pytest.raises(ValueError, match="sync_mode"):
        RecorderParams(sync_mode="bad")
