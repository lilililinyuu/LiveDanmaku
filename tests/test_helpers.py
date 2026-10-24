"""utils.helpers 单元测试。"""

import os
import sys
from pathlib import Path

from livedanmaku.utils.helpers import (
    _PATH_PREPENDED_MARKER,
    extract_emoji_names,
    prepend_venv_scripts_to_path,
)


def test_extract_emoji_names():
    assert extract_emoji_names("你好[face-blue-smiling]世界[heart]") == [
        "face-blue-smiling",
        "heart",
    ]
    assert extract_emoji_names("没有表情") == []


def test_prepend_venv_scripts_adds_path(monkeypatch, tmp_path):
    """sys.prefix 下存在 Scripts/yt-dlp.exe 时应把该目录前置到 PATH。"""
    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    (scripts / "yt-dlp.exe").write_bytes(b"fake")
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.delenv(_PATH_PREPENDED_MARKER, raising=False)
    monkeypatch.setenv("PATH", r"C:\Windows")

    prepend_venv_scripts_to_path()

    assert os.environ["PATH"].split(os.pathsep)[0] == str(scripts)
    assert os.environ[_PATH_PREPENDED_MARKER] == "1"


def test_prepend_venv_scripts_noop_without_tool(monkeypatch, tmp_path):
    """Scripts 下没有 yt-dlp 时不改 PATH（打包 EXE 场景自动跳过）。"""
    (tmp_path / "Scripts").mkdir()
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    monkeypatch.delenv(_PATH_PREPENDED_MARKER, raising=False)
    before = os.environ.get("PATH", "")

    prepend_venv_scripts_to_path()

    assert os.environ["PATH"] == before
    assert os.environ[_PATH_PREPENDED_MARKER] == "1"


def test_prepend_venv_scripts_idempotent(monkeypatch, tmp_path):
    """重复调用不应继续前置（每进程只生效一次）。"""
    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    (scripts / "yt-dlp.exe").write_bytes(b"fake")
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.delenv(_PATH_PREPENDED_MARKER, raising=False)
    monkeypatch.setenv("PATH", r"C:\Windows")

    prepend_venv_scripts_to_path()
    first = os.environ["PATH"]
    prepend_venv_scripts_to_path()

    assert os.environ["PATH"] == first
    assert Path(first.split(os.pathsep)[0]) == scripts
