"""下载指定片段单元测试。"""

import subprocess

from danmakustudio.core.pipeline import download_items
from danmakustudio.ui.worker import DownloadWorker


class _FakePopen:
    def __init__(self, stdout_lines=(), returncode=0):
        self.args = []
        self.stdout = iter(stdout_lines)
        self.stderr = iter([])
        self.returncode = returncode

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def communicate(self, *args, **kwargs):
        self.returncode = 0
        return (b"", b"")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.returncode = 0
        return False


def test_download_items_passes_sections(monkeypatch, tmp_path):
    """片段参数应转成 --download-sections "*start-end" 传给 yt-dlp。"""
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakePopen([str(tmp_path / "视频.mp4") + "\n"])

    monkeypatch.setattr("shutil.which", lambda _name: "/fake/bin")
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    download_items(
        "http://example.com", tmp_path, "b",
        want_video=True, want_chat=False, want_thumbnail=False,
        section="5:01-10:43",
    )

    cmd = captured["cmd"]
    assert "--download-sections" in cmd
    assert cmd[cmd.index("--download-sections") + 1] == "*5:01-10:43"
    # 界面层可能已带 "*" 前缀，不应重复叠加
    monkeypatch.setattr("shutil.which", lambda _name: "/fake/bin")

    def fake_popen2(cmd, **kwargs):
        captured["cmd2"] = cmd
        return _FakePopen([str(tmp_path / "视频2.mp4") + "\n"])

    monkeypatch.setattr(subprocess, "Popen", fake_popen2)
    download_items(
        "http://example.com", tmp_path, "b",
        want_video=True, want_chat=False, want_thumbnail=False,
        section="*11:16:45-11:20:55",
    )
    assert captured["cmd2"][captured["cmd2"].index("--download-sections") + 1] == "*11:16:45-11:20:55"
    assert "**" not in captured["cmd2"][captured["cmd2"].index("--download-sections") + 1]


def test_download_items_without_sections_omits_arg(monkeypatch, tmp_path):
    """未填片段时命令不应包含 --download-sections。"""
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakePopen([str(tmp_path / "视频.mp4") + "\n"])

    monkeypatch.setattr("shutil.which", lambda _name: "/fake/bin")
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    download_items(
        "http://example.com", tmp_path, "b",
        want_video=True, want_chat=False, want_thumbnail=False,
    )

    assert "--download-sections" not in captured["cmd"]


def test_download_worker_passes_section(qapp, tmp_path, monkeypatch):
    """DownloadWorker 应把片段参数透传给 download_items。"""
    captured = {}

    def fake_download_items(url, out_dir, fmt, **kwargs):
        captured["section"] = kwargs.get("section")
        return {"video": None, "chat": None, "thumbnail": None}

    monkeypatch.setattr(
        "danmakustudio.core.pipeline.download_items", fake_download_items
    )

    worker = DownloadWorker(
        "http://example.com", "b", tmp_path,
        want_video=True, want_chat=False, want_thumbnail=False,
        section="1:00-2:00",
    )
    worker.run()

    assert captured["section"] == "1:00-2:00"
