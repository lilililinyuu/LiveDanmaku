"""批量文件发现与字幕匹配测试。"""

from pathlib import Path

from danmakustudio.batch import collect_batch_jobs, find_matching_subtitle, iter_videos_in_folder


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return path


def test_find_matching_subtitle_prefers_xml(tmp_path):
    """同名 XML 和 LRC 同时存在时应优先选择 XML。"""
    video = _touch(tmp_path / "demo.mp4")
    xml = _touch(tmp_path / "demo.xml")
    _touch(tmp_path / "demo.lrc")

    assert find_matching_subtitle(video) == xml


def test_find_matching_subtitle_uses_lrc_without_xml(tmp_path):
    """只有 LRC 时应选择 LRC。"""
    video = _touch(tmp_path / "demo.mp4")
    lrc = _touch(tmp_path / "demo.lrc")

    assert find_matching_subtitle(video) == lrc


def test_iter_videos_in_folder_is_recursive(tmp_path):
    """文件夹选择应递归查找视频。"""
    first = _touch(tmp_path / "a.mp4")
    second = _touch(tmp_path / "nested" / "b.mkv")
    _touch(tmp_path / "nested" / "b.xml")
    _touch(tmp_path / "ignore.txt")

    assert iter_videos_in_folder(tmp_path) == [first, second]


def test_collect_batch_jobs_reports_missing_subtitles(tmp_path):
    """收集批量任务时应区分可处理任务和缺字幕视频。"""
    with_subtitle = _touch(tmp_path / "with.mp4")
    subtitle = _touch(tmp_path / "with.xml")
    missing = _touch(tmp_path / "missing.mp4")

    selection = collect_batch_jobs([tmp_path])

    assert len(selection.jobs) == 1
    assert selection.jobs[0].video_path == with_subtitle
    assert selection.jobs[0].subtitle_path == subtitle
    assert selection.jobs[0].output_path == tmp_path / "with-弹幕版.mp4"
    assert selection.missing_subtitles == [missing]


def test_iter_videos_in_folder_skips_unreadable_subfolders(tmp_path, monkeypatch):
    """个别子目录读取失败时，不应中断整个文件夹扫描。"""
    readable = _touch(tmp_path / "readable.mp4")
    broken_dir = tmp_path / "broken"
    broken_dir.mkdir()
    original_iterdir = Path.iterdir

    def fake_iterdir(path: Path):
        if path == broken_dir:
            raise OSError("permission denied")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", fake_iterdir)

    assert iter_videos_in_folder(tmp_path) == [readable]


def test_find_matching_subtitle_returns_none_when_directory_unreadable(tmp_path, monkeypatch):
    """字幕目录读取失败时应返回 None，而不是让批量收集崩溃。"""
    video = _touch(tmp_path / "demo.mp4")

    def fake_iterdir(path: Path):
        if path == tmp_path:
            raise OSError("permission denied")
        raise AssertionError("unexpected path")

    monkeypatch.setattr(Path, "iterdir", fake_iterdir)

    assert find_matching_subtitle(video) is None
