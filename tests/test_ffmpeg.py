"""FFmpeg 命令构建与进程收尾测试。"""

from __future__ import annotations

import io
import json
import subprocess
from collections import deque

import pytest

from danmakustudio.config.models import EncodeMode, EncodeParams, SystemParams
from danmakustudio.encode import ffmpeg as ffmpeg_module
from danmakustudio.encode.ffmpeg import FFmpegManager
from danmakustudio.errors import EncodeError
from danmakustudio.layout.params import LayerParams


def _make_manager(active_pipeline: str = EncodeMode.CPU) -> FFmpegManager:
    manager = FFmpegManager.__new__(FFmpegManager)
    manager.video_in = "input.webm"
    manager.video_out = "output.mp4"
    manager.encode_mode = active_pipeline
    manager.encode_params = EncodeParams()
    manager.system_params = SystemParams()
    manager.active_pipeline = active_pipeline
    manager.process = None
    manager.stderr_thread = None
    manager.stderr_tail = deque(maxlen=20)
    return manager


def test_clip_command_includes_ss_and_t():
    """测试片段：命令应包含 -ss（输入前）与 -t（时长）。"""
    manager = _make_manager(EncodeMode.CPU)
    cmd = manager.build_command(
        30.0, 1920, 1080, _layer_params(), in_ss=12.5, in_t=60.0,
    )

    assert "-ss" in cmd and "12.5" in cmd
    assert "-t" in cmd and "60.0" in cmd
    assert cmd.index("-ss") < cmd.index("-i")


def test_custom_ffmpeg_args_appended_before_output():
    """encode.extra_args 应追加在 ffmpeg 命令末尾、输出文件之前。"""
    manager = _make_manager(EncodeMode.CPU)
    manager.encode_params = EncodeParams(
        extra_args=["-preset", "faster", "-crf", "26", "-maxrate", "4M"],
    )

    cmd = manager.build_command(30.0, 1920, 1080, _layer_params())

    assert cmd[-1] == "output.mp4"
    joined = " ".join(cmd)
    assert "-preset faster" in joined
    assert "-crf 26" in joined
    assert "-maxrate 4M" in joined
    # 自定义参数必须位于输出文件之前
    assert joined.index("-maxrate 4M") < joined.index("output.mp4")


def _layer_params() -> LayerParams:
    return LayerParams(layer_x=10, layer_y=20, layer_w=640, layer_h=180)


def test_gpu_command_uses_generic_decode_and_nvenc_encode():
    manager = _make_manager(EncodeMode.GPU)
    cmd = manager.build_command(30.0, 1920, 1080, _layer_params())
    joined = " ".join(cmd)

    assert "h264_nvenc" in cmd
    assert "h264_cuvid" not in cmd
    assert "-hwaccel" not in cmd
    assert "scale_cuda" not in joined
    assert "format=yuv420p[out]" in joined


def test_qsv_command_keeps_software_overlay_before_qsv_encode():
    manager = _make_manager(EncodeMode.QSV)
    cmd = manager.build_command(30.0, 1920, 1080, _layer_params())
    joined = " ".join(cmd)

    assert "h264_qsv" in cmd
    assert "scale_qsv" not in joined
    assert "-hwaccel" not in cmd
    assert "format=nv12[out]" in joined


def test_qsv_command_pins_intel_adapter_by_default():
    """QSV 会话默认钉定真实 Intel 适配器：d3d11va 按 vendor_id 枚举，
    再以其为子设备创建 qsv 设备；设备参数是全局选项，须在首个 -i 之前。"""
    manager = _make_manager(EncodeMode.QSV)
    cmd = manager.build_command(30.0, 1920, 1080, _layer_params(), in_ss=5.0, in_t=60.0)
    joined = " ".join(cmd)

    assert "d3d11va=qsv_parent:,vendor_id=0x8086" in joined
    assert "qsv=qsv@qsv_parent" in joined
    assert cmd.index("-init_hw_device") < cmd.index("-ss") < cmd.index("-i")


@pytest.mark.parametrize("mode", [EncodeMode.CPU, EncodeMode.GPU, EncodeMode.QSV])
def test_all_pipelines_emit_progress_to_stderr(mode):
    """编码进度必须经 -progress pipe:2 汇报（背压与停转检测依赖它），
    -nostats 压掉普通统计行，避免污染错误诊断尾部。回归防护：
    漏加该标志时 encoded_frames 恒为 0，水位一到必触发假性停转。"""
    manager = _make_manager(mode)
    cmd = manager.build_command(30.0, 1920, 1080, _layer_params())

    assert cmd[cmd.index("-progress") + 1] == "pipe:2"
    assert "-nostats" in cmd


@pytest.mark.parametrize("mode", [EncodeMode.CPU, EncodeMode.GPU, EncodeMode.QSV])
def test_all_pipelines_bound_muxer_interleave_queue(mode):
    """音频流截断（直播录像常见）时，ffmpeg 7.x muxer 的交错队列会
    停止排出视频包并无界堆积（实测 2 分钟 40+GB）；必须压窄交错窗口。"""
    manager = _make_manager(mode)
    cmd = manager.build_command(30.0, 1920, 1080, _layer_params())

    i = cmd.index("-max_interleave_delta")
    assert cmd[i + 1] == "1000000"
    assert cmd[-1] == "output.mp4"


def test_qsv_command_without_vendor_id_keeps_auto_selection():
    """qsv_vendor_id=0 时不注入设备参数，走 ffmpeg 自动选择。"""
    manager = _make_manager(EncodeMode.QSV)
    manager.encode_params = EncodeParams(qsv_vendor_id=0)
    cmd = manager.build_command(30.0, 1920, 1080, _layer_params())

    assert "-init_hw_device" not in cmd


def test_qsv_check_probes_pinned_device_path(monkeypatch):
    """QSV 可用性探测与真实压制使用同样的钉定设备参数。"""
    manager = _make_manager(EncodeMode.QSV)
    calls: list[list[str]] = []

    class FakeCompleted:
        returncode = 0
        stdout = " V....D h264_qsv  NVIDIA/Intel Quick Sync H.264 (codec h264)"

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return FakeCompleted()

    monkeypatch.setattr(ffmpeg_module.subprocess, "run", fake_run)
    assert manager._check_qsv_available() is True

    probe = calls[-1]
    joined = " ".join(probe)
    assert "d3d11va=qsv_parent:,vendor_id=0x8086" in joined
    assert "qsv=qsv@qsv_parent" in joined


def test_qsv_check_fails_when_pinned_init_fails(monkeypatch):
    """钉定设备初始化失败时 QSV 视为不可用（AUTO 模式回退 CPU）。"""
    manager = _make_manager(EncodeMode.QSV)

    class FakeEncoders:
        returncode = 0
        stdout = " V....D h264_qsv  (codec h264)"

    class FakeFailed:
        returncode = 1
        stdout = ""

    def fake_run(cmd, **kwargs):
        return FakeEncoders() if "-encoders" in cmd else FakeFailed()

    monkeypatch.setattr(ffmpeg_module.subprocess, "run", fake_run)
    assert manager._check_qsv_available() is False


def test_get_video_info_handles_na_nb_frames(monkeypatch):
    manager = _make_manager()
    run_kwargs = {}
    ffprobe_data = {
        "streams": [{
            "width": 1919,
            "height": 1079,
            "avg_frame_rate": "30000/1001",
            "r_frame_rate": "60000/1001",
            "nb_frames": "N/A",
            "duration": "10.02",
        }],
        "format": {},
    }

    def fake_run(*_args, **kwargs):
        run_kwargs.update(kwargs)
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(ffprobe_data),
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    info = manager.get_video_info()

    assert info["w"] == 1919
    assert info["h"] == 1079
    assert info["fps"] == pytest.approx(30000 / 1001)
    assert info["frames"] == 301
    assert run_kwargs["creationflags"] == ffmpeg_module._CREATE_NO_WINDOW


def test_start_hides_ffmpeg_console(monkeypatch):
    manager = _make_manager()
    popen_kwargs = {}

    class FakeProcess:
        stdin = io.BytesIO()
        stderr = io.BytesIO()

    def fake_popen(*_args, **kwargs):
        popen_kwargs.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    manager.start(["ffmpeg", "-version"])
    if manager.stderr_thread is not None:
        manager.stderr_thread.join(timeout=1)

    assert popen_kwargs["creationflags"] == ffmpeg_module._CREATE_NO_WINDOW


def test_cleanup_raises_when_ffmpeg_returns_nonzero():
    manager = _make_manager()
    manager.stderr_tail.append("muxing failed")

    class FakeProcess:
        stdin = io.BytesIO()
        stderr = io.BytesIO()

        def wait(self, timeout=None):
            return 1

        def kill(self):
            raise AssertionError("kill should not be called")

    manager.process = FakeProcess()

    with pytest.raises(EncodeError, match="退出码 1"):
        manager.cleanup()

    assert manager.process is None


def test_parse_progress_frame():
    """-progress 输出行解析：frame=N 提取帧数，其余行返回 None。"""
    from danmakustudio.encode.ffmpeg import FFmpegManager

    assert FFmpegManager._parse_progress_frame("frame=123") == 123
    assert FFmpegManager._parse_progress_frame("frame=0") == 0
    assert FFmpegManager._parse_progress_frame("fps=42") is None
    assert FFmpegManager._parse_progress_frame("bitrate=N/A") is None
    assert FFmpegManager._parse_progress_frame("frame=N/A") is None
    assert FFmpegManager._parse_progress_frame("progress=continue") is None


def test_progress_updates_encoded_frames(monkeypatch):
    """stderr 进度行（-progress pipe:2）应更新 encoded_frames。"""

    from danmakustudio.encode.ffmpeg import FFmpegManager

    class _FakeStream:
        def __init__(self, text):
            self._text = text

        def __iter__(self):
            # _read_stderr 会对每行做 .decode()，这里产出 bytes 行
            return iter(self._text.encode().splitlines(keepends=True))

        def read(self, n=-1):
            t, self._text = self._text, ""
            return t

    class _FakeProc:
        def __init__(self):
            # -progress pipe:2 的进度行走 stderr
            self.stdout = _FakeStream("")
            self.stderr = _FakeStream("frame=10\nframe=42\n")
            self.pid = 0

    mgr = FFmpegManager("in.mp4", "out.mp4")
    monkeypatch.setattr(
        "danmakustudio.encode.ffmpeg.subprocess.Popen",
        lambda cmd, **kw: _FakeProc(),
    )

    mgr.start(["ffmpeg", "-version"])
    import time

    deadline = time.monotonic() + 5
    while mgr.encoded_frames < 42 and time.monotonic() < deadline:
        time.sleep(0.05)

    assert mgr.encoded_frames == 42
