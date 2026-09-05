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


class _FakePopen:
    """模拟录制进程：stdout 输出若干行后结束。"""

    def __init__(self, lines, output_dir: Path | None = None):
        self.args = []
        self.stdout = iter(lines)
        self.stderr = iter([])
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
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.returncode = 0
        return False

    def communicate(self, *args, **kwargs):
        return (b"", b"")


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
    assert "--write-subs" in cmd and "--sub-langs" in cmd and "live_chat" in cmd
    assert "-o" in cmd
    assert cmd[-1] == "https://www.youtube.com/watch?v=abc"
    # -o 的值紧跟其后，指向输出目录
    assert str(tmp_path / "rec") in cmd[cmd.index("-o") + 1]


def test_build_record_command_minimal(tmp_path):
    """可选项目关闭时对应参数不应出现。"""
    cmd = build_record_command(
        _params(
            tmp_path,
            live_from_start=False,
            proxy="",
            pot_token_script="",
            record_chat=False,
            fmt="best",
        )
    )
    assert "--live-from-start" not in cmd
    assert "--proxy" not in cmd
    assert "--extractor-args" not in cmd
    assert "--write-subs" not in cmd
    # 单文件格式无需合并容器
    assert "--merge-output-format" not in cmd


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
    session.wait()

    assert any("已开始录制" in line for line in received)
    files = session.current_files()
    assert len(files) == 1 and files[0].name == "20260905_测试直播.mp4"
    assert session.total_size == 128
    assert session.elapsed_seconds >= 0


def test_record_session_stop_sends_ctrl_break(tmp_path, monkeypatch):
    """停止录制应先发送 CTRL_BREAK 优雅收尾。"""
    params = _params(tmp_path)
    session = RecordSession(params)

    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: _FakePopen([]))
    session.start()
    session.stop()

    assert session.process.signals[0] == _CTRL_BREAK


def test_record_session_save_snapshot(tmp_path, monkeypatch):
    """保存片段应把当前输出文件复制到目标目录且不改变录制状态。"""
    params = _params(tmp_path)
    session = RecordSession(params)

    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: _FakePopen([]))
    session.start()
    (params.output_dir / "20260905_测试直播.mp4.part").write_bytes(b"x" * 64)

    copied = session.save_snapshot()

    assert len(copied) == 1
    assert not copied[0].name.endswith(".part")
    assert "已保存" in str(copied[0].parent)
    assert copied[0].stat().st_size == 64


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
    video = params.output_dir / "直播.f137.mp4"
    audio = params.output_dir / "直播.f140.m4a"
    video.write_bytes(b"fake-video")
    audio.write_bytes(b"fake-audio")

    copied = session.save_snapshot()

    names = [f.name for f in copied]
    # 合并失败时原始两个片段都被保留（mp4 + m4a）
    assert any(n.endswith(".mp4") and n != "" for n in names)
    assert any(n.endswith(".m4a") for n in names)
