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


def test_download_items_video_only_command(monkeypatch, tmp_path):
    captured = {}

    class _Result:
        returncode = 0
        stdout = str(tmp_path / "视频.mp4") + "\n"
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Result()

    monkeypatch.setattr("shutil.which", lambda _name: "/fake/bin")
    monkeypatch.setattr(subprocess, "run", fake_run)

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

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Result()

    monkeypatch.setattr("shutil.which", lambda _name: "/fake/bin")
    monkeypatch.setattr(subprocess, "run", fake_run)

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
