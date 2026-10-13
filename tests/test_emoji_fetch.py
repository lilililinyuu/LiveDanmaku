"""emoji_fetch 下载器测试。"""

import urllib.request

from livedanmaku.render.emoji_fetch import download_emoji


class FakeResponse:
    """模拟 urlopen 的上下文管理器响应。"""

    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args) -> bool:
        return False


def test_download_emoji_writes_and_caches(monkeypatch, tmp_path):
    calls = []

    def fake_urlopen(url, timeout):
        calls.append(url)
        return FakeResponse(b"png-bytes")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    path = download_emoji("face-blue-smiling", "http://example.com/e.png", cache_dir=tmp_path)
    assert path is not None
    assert path.read_bytes() == b"png-bytes"
    assert len(calls) == 1

    # 再次下载命中磁盘缓存，不再发起网络请求
    path2 = download_emoji("face-blue-smiling", "http://example.com/e.png", cache_dir=tmp_path)
    assert path2 == path
    assert len(calls) == 1


def test_download_emoji_rejects_non_http_url(tmp_path):
    path = download_emoji("x", "file:///etc/passwd", cache_dir=tmp_path)
    assert path is None


def test_download_emoji_returns_none_on_error(monkeypatch, tmp_path):
    def fail_urlopen(url, timeout):
        raise OSError("network down")

    monkeypatch.setattr(urllib.request, "urlopen", fail_urlopen)

    path = download_emoji("x", "http://example.com/e.png", cache_dir=tmp_path)
    assert path is None
