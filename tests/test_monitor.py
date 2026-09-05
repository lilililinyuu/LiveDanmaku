"""频道开播监控模块单元测试。"""

import subprocess

from danmakustudio.core.monitor import probe_channel_live


class _FakeRunResult:
    def __init__(self, stdout, returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def test_probe_channel_live_detects_live(monkeypatch):
    """is_live=True 时应返回直播视频 URL 与标题。"""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeRunResult("abc123|True|is_live|【直播】测试\n")

    monkeypatch.setattr(subprocess, "run", fake_run)

    url, title = probe_channel_live(
        "https://www.youtube.com/@handle",
        proxy="socks5://127.0.0.1:7897",
        pot_token_script=r"D:\bgutil\bgutil-pot.exe",
    )

    assert url == "https://www.youtube.com/watch?v=abc123"
    assert title == "【直播】测试"
    # 探测页自动指向 /live，代理与 PO token 参数齐备
    assert captured["cmd"][-1].endswith("/live")
    assert "--proxy" in captured["cmd"]
    assert "--extractor-args" in captured["cmd"]


def test_probe_channel_live_not_live(monkeypatch):
    """未开播（is_live=False）应返回 None。"""

    def fake_run(cmd, **kwargs):
        return _FakeRunResult("xyz789|False|not_live|旧视频\n")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert probe_channel_live("https://www.youtube.com/@handle") == (None, "")


def test_probe_channel_live_parse_failure_is_not_live(monkeypatch):
    """探测失败（如频道未开播导致解析失败）应视为未开播。"""

    def fake_run(cmd, **kwargs):
        return _FakeRunResult("", returncode=1, stderr="ERROR: ...")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert probe_channel_live("https://www.youtube.com/@handle") == (None, "")


def test_probe_channel_live_timeout(monkeypatch):
    """探测超时应视为未开播而不是抛异常。"""

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 90)

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert probe_channel_live("https://www.youtube.com/@handle") == (None, "")
