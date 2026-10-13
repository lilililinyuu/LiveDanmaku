"""pipeline 下载辅助单元测试（yt-dlp 下载为图形界面「下载内容」页所用）。"""

import subprocess
from pathlib import Path

import pytest

from livedanmaku.core.pipeline import (
    download_items,
    parse_ytdlp_paths,
)
from livedanmaku.errors import LiveDanmakuError


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
    with pytest.raises(LiveDanmakuError, match="未选择"):
        download_items(
            "http://example.com", Path("."), "b",
            want_video=False, want_chat=False, want_thumbnail=False,
        )


class _FakeStream:
    """供 _iter_output_lines 读取的伪流：read() 一次性返回全部文本。"""

    def __init__(self, text=""):
        self._text = text

    def read(self, n=-1):
        t, self._text = self._text, ""
        return t


class _FakePopen:
    """模拟 yt-dlp 进程：stdout 按行输出，stderr 为空。"""

    def __init__(self, stdout_lines, returncode=0):
        text = "\n".join(stdout_lines) + ("\n" if stdout_lines else "")
        self.stdout = _FakeStream(text)
        self.stderr = _FakeStream()
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
    with pytest.raises(LiveDanmakuError, match="封面"):
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

    with pytest.raises(LiveDanmakuError, match="视频下载失败"):
        download_items(
            "http://example.com", tmp_path, "b",
            want_video=True, want_chat=False, want_thumbnail=False,
        )


def test_download_items_chat_fallback_from_dir(monkeypatch, tmp_path):
    """stdout 未打印弹幕 JSON 路径时，应从下载目录的新增文件回退查找。"""

    def fake_popen(cmd, **kwargs):
        # 模拟 yt-dlp：只打印视频路径，弹幕 JSON 由其直接写入目录
        chat = tmp_path / "【直播】x.live_chat.json"
        chat.write_text("{}", encoding="utf-8")
        return _FakePopen([str(tmp_path / "【直播】x.mp4") + "\n"])

    monkeypatch.setattr("shutil.which", lambda _name: "/fake/bin")
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = download_items(
        "http://example.com", tmp_path, "b",
        want_video=True, want_chat=True, want_thumbnail=False,
    )

    assert result["video"] is not None and result["video"].suffix == ".mp4"
    assert result["chat"] is not None
    assert result["chat"].name.endswith(".live_chat.json")


def test_download_items_chat_fallback_prefers_plain_json(monkeypatch, tmp_path):
    """无 .live_chat 命名时回退到普通 .json 新增文件。"""

    def fake_popen(cmd, **kwargs):
        (tmp_path / "x.json").write_text("{}", encoding="utf-8")
        return _FakePopen([])

    monkeypatch.setattr("shutil.which", lambda _name: "/fake/bin")
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = download_items(
        "http://example.com", tmp_path, "b",
        want_video=False, want_chat=True, want_thumbnail=False,
    )

    assert result["chat"] is not None
    assert result["chat"].name == "x.json"


def test_download_items_partial_success(monkeypatch, tmp_path):
    """yt-dlp 异常退出但弹幕 JSON 已保存时应按部分成功返回，不报错。"""

    def fake_popen(cmd, **kwargs):
        chat = tmp_path / "【直播】x.live_chat.json"
        chat.write_text("{}", encoding="utf-8")
        return _FakePopen([], returncode=1)   # 视频中途失败

    monkeypatch.setattr("shutil.which", lambda _name: "/fake/bin")
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = download_items(
        "http://example.com", tmp_path, "b",
        want_video=True, want_chat=True, want_thumbnail=False,
    )

    assert result["video"] is None
    assert result["chat"] is not None
    assert result["chat"].name.endswith(".live_chat.json")


def test_download_items_total_failure_raises(monkeypatch, tmp_path):
    """什么都没下载到时仍应报下载失败。"""

    def fake_popen(cmd, **kwargs):
        return _FakePopen([], returncode=1)

    monkeypatch.setattr("shutil.which", lambda _name: "/fake/bin")
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    with pytest.raises(LiveDanmakuError, match="视频下载失败"):
        download_items(
            "http://example.com", tmp_path, "b",
            want_video=True, want_chat=True, want_thumbnail=False,
        )


def test_download_items_passes_proxy_flag(monkeypatch, tmp_path):
    """proxy 非空时，视频与弹幕命令都应包含 --proxy。"""
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured.setdefault("cmds", []).append(cmd)
        return _FakePopen([str(tmp_path / "视频.mp4") + "\n"])

    monkeypatch.setattr("shutil.which", lambda _name: "/fake/bin")
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    download_items(
        "http://example.com", tmp_path, "b",
        want_video=True, want_chat=True, want_thumbnail=False,
        proxy="http://127.0.0.1:7897",
    )

    for cmd in captured["cmds"]:
        assert "--proxy" in cmd
        assert "http://127.0.0.1:7897" in cmd


def test_iter_output_lines_splits_carriage_returns():
    """\r 刷新的 ffmpeg/yt-dlp 进度应被拆成独立行。"""
    from livedanmaku.core.pipeline import _iter_output_lines

    class Stream:
        text = "[download] 10%\r[download] 50%\r[download] 100%\nfilepath"

        def read(self, n=-1):
            t, self.text = self.text, ""
            return t

    lines = list(_iter_output_lines(Stream()))
    assert lines == [
        "[download] 10%",
        "[download] 50%",
        "[download] 100%",
        "filepath",
    ]
