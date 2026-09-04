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
