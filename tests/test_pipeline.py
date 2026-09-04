"""pipeline 下载辅助单元测试（yt-dlp 下载为图形界面「下载内容」页所用）。"""

import subprocess
from pathlib import Path

import pytest

from danmakustudio.core.pipeline import (
    download_items,
    parse_ytdlp_paths,
)
from danmakustudio.errors import DanmakuStudioError


def test_parse_ytdlp_paths():
    video, chat = parse_ytdlp_paths([
        r"C:\work\【直播】某视频.f136.mp4",
        r"C:\work\【直播】某视频.live_chat.json",
        "",
    ])
    assert video is not None and video.suffix == ".mp4"
    assert chat is not None and ".live_chat" in chat.name


def test_parse_ytdlp_paths_missing_chat():
    video, chat = parse_ytdlp_paths([r"C:\work\video.mp4"])
    assert video is not None
    assert chat is None


def test_parse_ytdlp_paths_same_name_json():
    """弹幕 JSON 与视频同名（仅后缀不同）时也应被识别。"""
    video, chat = parse_ytdlp_paths([
        r"C:\work\【直播】某视频.mp4",
        r"C:\work\【直播】某视频.json",
    ])
    assert video is not None and video.suffix == ".mp4"
    assert chat is not None and chat.suffix == ".json"
    assert chat.stem == video.stem


def test_download_items_no_selection():
    with pytest.raises(DanmakuStudioError, match="未选择"):
        download_items(
            "http://example.com", Path("."), "b",
            want_video=False, want_chat=False, want_thumbnail=False,
        )


class _FakePopen:
    """模拟 yt-dlp 进程：stdout 按行输出，stderr 为空。"""

    def __init__(self, stdout_lines, returncode=0):
        self.stdout = iter(stdout_lines)
        self.stderr = iter([])
        self.returncode = returncode

    def wait(self):
        return self.returncode


def test_download_items_video_only_command(monkeypatch, tmp_path):
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakePopen([str(tmp_path / "视频.mp4") + "\n"])

    monkeypatch.setattr("shutil.which", lambda _name: "/fake/bin")
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = download_items(
        "http://example.com", tmp_path, "b",
        want_video=True, want_chat=False, want_thumbnail=False,
    )

    cmd = captured["cmd"]
    assert "-f" in cmd and "b" in cmd
    assert "--write-subs" not in cmd
    assert "--write-thumbnail" not in cmd
    assert "--skip-download" not in cmd
    assert result["video"] is not None
    assert result["chat"] is None
    assert result["thumbnail"] is None


def test_download_items_thumbnail_only_command(monkeypatch, tmp_path):
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakePopen([])

    monkeypatch.setattr("shutil.which", lambda _name: "/fake/bin")
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    # 未创建真实封面文件 → 应报「未找到封面」；但命令已可断言
    with pytest.raises(DanmakuStudioError, match="封面"):
        download_items(
            "http://example.com", tmp_path, "b",
            want_video=False, want_chat=False, want_thumbnail=True,
        )

    cmd = captured["cmd"]
    assert "--skip-download" in cmd
    assert "--write-thumbnail" in cmd
    assert "-f" not in cmd
    assert "--write-subs" not in cmd


def test_download_items_streams_output(monkeypatch, tmp_path):
    """on_output 应回调 yt-dlp 的每一行输出（含进度行）。"""
    received = []

    def fake_popen(cmd, **kwargs):
        return _FakePopen([
            "[download]   5.0% of ~ 1.00GiB",
            "[download]  100.0% of ~ 1.00GiB",
            str(tmp_path / "视频.mp4"),
        ])

    monkeypatch.setattr("shutil.which", lambda _name: "/fake/bin")
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    download_items(
        "http://example.com", tmp_path, "b",
        want_video=True, want_chat=False, want_thumbnail=False,
        on_output=received.append,
    )

    assert received[0].startswith("[download]   5.0%")
    assert len(received) == 3


def test_download_items_nonzero_exit(monkeypatch, tmp_path):
    """yt-dlp 失败退出时应抛出下载失败错误。"""

    def fake_popen(cmd, **kwargs):
        return _FakePopen([], returncode=1)

    monkeypatch.setattr("shutil.which", lambda _name: "/fake/bin")
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    with pytest.raises(DanmakuStudioError, match="yt-dlp 下载失败"):
        download_items(
            "http://example.com", tmp_path, "b",
            want_video=True, want_chat=False, want_thumbnail=False,
        )
