"""pipeline 编排单元测试。"""

import subprocess
from pathlib import Path

import pytest

from danmakustudio.config import DEFAULT_CONFIG
from danmakustudio.core.pipeline import (
    TRIM_REF_WINDOW,
    Workbench,
    _confirm,
    download_items,
    download_stream,
    parse_trim_count,
    parse_trim_keep_index,
    parse_ytdlp_paths,
    step_trim_head,
    trim_reference_lines,
)
from danmakustudio.errors import DanmakuStudioError
from danmakustudio.input.event import DanmakuEvent


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


def test_confirm_yes_flag(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert _confirm("问?", default=False, yes=True) is True


def test_confirm_answers(monkeypatch):
    answers = iter(["y", "N", "", "yes"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    assert _confirm("问?") is True
    assert _confirm("问?") is False
    assert _confirm("问?", default=True) is True   # 回车取默认
    assert _confirm("问?") is True


def test_download_stream_missing_ytdlp(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _name: None)
    with pytest.raises(DanmakuStudioError, match="yt-dlp"):
        download_stream("http://example.com", Path("."), "b")


def test_download_stream_command(monkeypatch, tmp_path):
    captured = {}

    class _Result:
        returncode = 0
        stdout = str(tmp_path / "视频.mp4") + "\n" + str(tmp_path / "视频.live_chat.json") + "\n"
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _Result()

    monkeypatch.setattr("shutil.which", lambda _name: "/fake/yt-dlp")
    monkeypatch.setattr(subprocess, "run", fake_run)

    video, chat = download_stream("http://example.com/watch?v=x", tmp_path, "b")

    cmd = captured["cmd"]
    assert cmd[0].endswith("yt-dlp")
    assert "--write-subs" in cmd
    assert "--sub-langs" in cmd and "live_chat" in cmd
    assert "--print" in cmd and "after_move:filepath" in cmd
    assert video.suffix == ".mp4"
    assert ".live_chat" in chat.name


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


# =============================================================================
# 步骤 4：删除开头弹幕
# =============================================================================


def _make_workbench(tmp_path: Path) -> Workbench:
    return Workbench(source="", out_dir=tmp_path, config=DEFAULT_CONFIG)


def _write_trim_xml(tmp_path: Path) -> Path:
    xml = tmp_path / "chat.xml"
    xml.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<i>\n'
        '  <d p="1,1,25,16777215,0,0,0,0" user="u1">测试弹幕一</d>\n'
        '  <d p="2.5,1,25,16777215,0,0,0,0" user="u2">测试弹幕二</d>\n'
        '  <d p="4,1,25,16777215,0,0,0,0" member="1" user="u3">正式弹幕</d>\n'
        "</i>\n",
        encoding="utf-8",
    )
    return xml


def test_parse_trim_count():
    assert parse_trim_count("5", 10) == 5
    assert parse_trim_count(" 3 ", 10) == 3
    assert parse_trim_count("0", 10) == 0   # 0 = 撤销删除


def test_parse_trim_count_invalid():
    with pytest.raises(ValueError):
        parse_trim_count("abc", 10)
    with pytest.raises(ValueError):
        parse_trim_count("-1", 10)
    with pytest.raises(ValueError):   # 会删除全部
        parse_trim_count("10", 10)


def test_parse_trim_keep_index():
    assert parse_trim_keep_index("12", 50) == 11
    assert parse_trim_keep_index("#3", 50) == 2


def test_parse_trim_keep_index_invalid():
    with pytest.raises(ValueError):
        parse_trim_keep_index("0", 50)
    with pytest.raises(ValueError):
        parse_trim_keep_index(str(TRIM_REF_WINDOW + 1), 50)
    with pytest.raises(ValueError):
        parse_trim_keep_index("abc", 50)


def test_parse_trim_keep_index_window_capped_by_total():
    """弹幕不足 window 条时，序号上限应取弹幕总数。"""
    assert parse_trim_keep_index("3", 3) == 2
    with pytest.raises(ValueError):
        parse_trim_keep_index("4", 3)


def test_trim_reference_lines():
    events = [
        DanmakuEvent(time=1.0, user=f"用户{i}", text=f"第{i}条弹幕内容")
        for i in range(1, 4)
    ]
    lines = trim_reference_lines(events)
    assert len(lines) == 3
    assert lines[0].strip().startswith("1.")
    assert "[1.0s]" in lines[0] and "用户1" in lines[0] and "第1条弹幕内容" in lines[0]


def test_trim_reference_lines_truncates_long_text():
    long_text = "超" * 60
    lines = trim_reference_lines([DanmakuEvent(time=0.0, user="u", text=long_text)])
    assert "…" in lines[0]
    assert long_text not in lines[0]


def test_trim_reference_lines_window_cap():
    events = [DanmakuEvent(time=float(i), user="u", text=f"m{i}") for i in range(60)]
    assert len(trim_reference_lines(events)) == TRIM_REF_WINDOW


def test_step_trim_head_numeric(monkeypatch, tmp_path, capsys):
    xml = _write_trim_xml(tmp_path)
    wb = _make_workbench(tmp_path)
    wb.xml_path = xml
    monkeypatch.setattr("builtins.input", lambda _: "2")

    step_trim_head(wb)

    assert wb.drop_first == 2
    out = capsys.readouterr().out
    assert "剩余 1 条" in out and "正式弹幕" in out


def test_step_trim_head_reference(monkeypatch, tmp_path, capsys):
    xml = _write_trim_xml(tmp_path)
    wb = _make_workbench(tmp_path)
    wb.xml_path = xml
    answers = iter(["r", "3"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))

    step_trim_head(wb)

    assert wb.drop_first == 2
    out = capsys.readouterr().out
    assert "u1" in out and "测试弹幕一" in out   # 参照列表已展示


def test_step_trim_head_cancel(monkeypatch, tmp_path):
    wb = _make_workbench(tmp_path)
    wb.xml_path = _write_trim_xml(tmp_path)
    answers = iter(["r", "c"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))

    step_trim_head(wb)

    assert wb.drop_first == 0


def test_step_trim_head_invalid_input_keeps_state(monkeypatch, tmp_path):
    wb = _make_workbench(tmp_path)
    wb.xml_path = _write_trim_xml(tmp_path)
    wb.drop_first = 1
    monkeypatch.setattr("builtins.input", lambda _: "abc")

    step_trim_head(wb)

    assert wb.drop_first == 1


def test_step_trim_head_reset_zero(monkeypatch, tmp_path):
    wb = _make_workbench(tmp_path)
    wb.xml_path = _write_trim_xml(tmp_path)
    wb.drop_first = 2
    monkeypatch.setattr("builtins.input", lambda _: "0")

    step_trim_head(wb)

    assert wb.drop_first == 0


def test_step_trim_head_missing_xml(tmp_path):
    wb = _make_workbench(tmp_path)
    with pytest.raises(DanmakuStudioError, match="XML"):
        step_trim_head(wb)
