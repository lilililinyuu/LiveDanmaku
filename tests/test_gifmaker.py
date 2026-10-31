"""gifmaker（视频转 GIF）单元测试：命令构建、参数校验、停止路径。"""

import io
import shutil
import subprocess
from pathlib import Path

import pytest

from livedanmaku.core import gifmaker
from livedanmaku.core.gifmaker import build_gif_command, make_gif, probe_video_summary
from livedanmaku.errors import LiveDanmakuError


def _probe_info():
    # 模拟 FFmpegManager.probe_video 的返回形状（duration 由 summary 计算）
    return {"w": 1920, "h": 1080, "fps": 60.0, "frames": 3600}


def test_build_gif_command_high_quality():
    cmd = build_gif_command(
        Path("in.mp4"), Path("out.gif"),
        start=2.5, clip_duration=8.0, speed=2.0, fps=15,
        max_width=480,
        palettegen_opts="stats_mode=diff:reserve_transparent=on",
        paletteuse_opts="dither=sierra2_4a:alpha_threshold=128",
    )
    joined = " ".join(cmd)
    # 输入 seek + 片段时长
    assert "-ss" in cmd and "2.500" in cmd
    assert "-t" in cmd and "8.000" in cmd
    # 倍速经 setpts=PTS/倍速 实现（除以倍速）
    assert "setpts=PTS/2.0" in joined
    assert "fps=15" in joined
    assert "scale=480:-2" in joined
    assert "format=rgba" in joined  # 保住 alpha 进入调色板阶段
    assert "reserve_transparent=on" in joined
    assert "alpha_threshold=128" in joined
    assert "palettegen=stats_mode=diff" in joined
    assert "paletteuse=dither=sierra2_4a" in joined
    assert "-loop" in cmd and "0" in cmd
    assert cmd[-1] == "out.gif"
    assert "-c:v" not in cmd  # 非 webm 不强制解码器


def test_build_gif_command_webm_forces_vpx_decoder():
    cmd = build_gif_command(
        Path("in.webm"), Path("out.gif"),
        start=0.0, clip_duration=5.0, speed=1.0, fps=15,
        max_width=0,
        palettegen_opts="stats_mode=diff:reserve_transparent=on",
        paletteuse_opts="dither=bayer:bayer_scale=4:alpha_threshold=128",
        force_vpx_decoder=True,
    )
    # VP9 alpha webm 的 alpha 层只有 libvpx 解码器会合并，必须在 -i 前强制
    assert "-c:v" in cmd and cmd[cmd.index("-c:v") + 1] == "libvpx-vp9"
    assert cmd.index("-c:v") < cmd.index("-i")


def test_build_gif_command_original_width_and_low_quality():
    cmd = build_gif_command(
        Path("in.mp4"), Path("out.gif"),
        start=0.0, clip_duration=5.0, speed=1.0, fps=10,
        max_width=0,
        palettegen_opts="stats_mode=diff:max_colors=64:reserve_transparent=on",
        paletteuse_opts="dither=bayer:bayer_scale=5:alpha_threshold=128",
    )
    assert "scale=iw:-2" in " ".join(cmd)
    assert "max_colors=64" in " ".join(cmd)


def _patch_probe(monkeypatch):
    monkeypatch.setattr(
        gifmaker.FFmpegManager, "probe_video",
        staticmethod(lambda _p: _probe_info()),
    )


def _patch_popen_capture(monkeypatch, captures: list):
    class FakeProc:
        def __init__(self, *args, **kwargs):
            captures.append(list(args[0]) if args else [])
            self.stdout = io.StringIO("")
            self.stderr = io.StringIO("")
            self.returncode = 0

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(gifmaker.subprocess, "Popen", FakeProc)


def test_make_gif_validates_and_builds(monkeypatch, tmp_path):
    """校验通过后应构建正确命令并产出 GIF 路径与时长信息。"""
    _patch_probe(monkeypatch)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    out = tmp_path / "out.gif"
    captures: list = []
    _patch_popen_capture(monkeypatch, captures)

    def fake_unlink_ok():
        return True

    out.write_bytes(b"gif")  # 模拟产出文件

    result = make_gif(
        video, out,
        start=1.0, end=11.0, speed=2.0, fps=15, resolution="480p",
        quality="high", force=True,
    )
    assert result["stopped"] is False
    assert result["clip_duration"] == pytest.approx(10.0)
    assert result["output_duration"] == pytest.approx(5.0)
    assert len(captures) == 1 and "scale=854:-2" in " ".join(captures[0])


def test_make_gif_rejects_bad_params(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    with pytest.raises(LiveDanmakuError, match="倍速"):
        make_gif(video, quality="medium", speed=8.0, end=10.0, force=True)
    with pytest.raises(LiveDanmakuError, match="帧率"):
        make_gif(video, quality="medium", fps=60, end=10.0, force=True)
    with pytest.raises(LiveDanmakuError, match="调色板档位"):
        make_gif(video, quality="ultra", end=10.0, force=True)
    with pytest.raises(LiveDanmakuError, match="分辨率"):
        make_gif(video, resolution="900p", end=10.0, force=True)


def test_make_gif_rejects_bad_range(monkeypatch, tmp_path):
    _patch_probe(monkeypatch)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    with pytest.raises(LiveDanmakuError, match="太短"):
        make_gif(video, tmp_path / "o.gif", start=59.95, end=60.0, force=True)


def test_make_gif_clamps_end_to_duration(monkeypatch, tmp_path):
    """end 超过视频时长时钳制到时长。"""
    _patch_probe(monkeypatch)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    out = tmp_path / "o.gif"
    out.write_bytes(b"gif")
    captures: list = []
    _patch_popen_capture(monkeypatch, captures)

    result = make_gif(video, out, start=50.0, end=999.0, force=True)

    assert result["clip_duration"] == pytest.approx(10.0)  # 60 - 50


def test_make_gif_missing_video_raises(tmp_path):
    with pytest.raises(LiveDanmakuError):
        make_gif(tmp_path / "missing.mp4", tmp_path / "o.gif", end=5.0, force=True)


def test_probe_video_summary(monkeypatch, tmp_path):
    _patch_probe(monkeypatch)
    info = probe_video_summary(tmp_path / "v.mp4")
    assert info["duration"] == pytest.approx(60.0)
    assert info["width"] == 1920 and info["height"] == 1080


def _has_libvpx_vp9() -> bool:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        capture_output=True, text=True, creationflags=flags, check=False,
    )
    return "libvpx-vp9" in r.stdout


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not _has_libvpx_vp9(),
    reason="需要 ffmpeg 与 libvpx-vp9 编码器",
)
def test_make_gif_preserves_webm_transparency(tmp_path):
    """端到端：透明背景 VP9 webm 转 GIF 后背景仍透明。

    VP9 alpha 存在 BlockAdditions 附加层，ffmpeg 默认解码器会忽略
    （背景变黑），必须强制 libvpx-vp9 解码器。
    """
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    webm = tmp_path / "trans.webm"
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=red@0.0:s=320x240:d=1,format=rgba",
            "-f", "lavfi", "-i", "color=c=green@0.5:s=160x120:d=1,format=rgba",
            "-filter_complex",
            "[0][1]overlay=(W-w)/2:(H-h)/2,format=yuva420p",
            "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", str(webm),
        ],
        capture_output=True, check=False, creationflags=flags,
    )
    assert webm.exists() and webm.stat().st_size > 0

    result = make_gif(
        webm, tmp_path / "out.gif",
        start=0.0, end=1.0, speed=1.0, fps=15,
        resolution="original", quality="medium", force=True,
    )

    assert result["stopped"] is False
    gif = result["gif"]
    assert gif is not None and gif.exists() and gif.stat().st_size > 0

    decode = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", str(gif), "-frames:v", "1",
            "-f", "rawvideo", "-pix_fmt", "rgba", "-",
        ],
        capture_output=True, creationflags=flags, check=False,
    )
    assert len(decode.stdout) >= 4
    corner_alpha = decode.stdout[3]
    assert corner_alpha == 0, f"GIF 背景不透明（alpha={corner_alpha}），透明丢失"
