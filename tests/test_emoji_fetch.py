"""emoji_fetch 下载器测试。"""

import socket

from livedanmaku.render import emoji_fetch
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

    def fake_open(url, timeout):
        calls.append(url)
        return FakeResponse(b"png-bytes")

    # 强制直连分支：无系统代理 + 公网解析，保证测试确定性（不触网）
    monkeypatch.setattr(
        emoji_fetch.urllib.request, "getproxies", dict
    )

    def fake_getaddrinfo(host, port, proto=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("142.250.196.33", 443))]

    monkeypatch.setattr(emoji_fetch.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(emoji_fetch, "_open_url", fake_open)

    url = "https://yt3.ggpht.com/e.png"
    path = download_emoji("face-blue-smiling", url, cache_dir=tmp_path)
    assert path is not None
    assert path.read_bytes() == b"png-bytes"
    assert calls == [url]

    # 再次下载命中磁盘缓存，不再发起网络请求
    path2 = download_emoji("face-blue-smiling", url, cache_dir=tmp_path)
    assert path2 == path
    assert len(calls) == 1


def test_download_emoji_rejects_non_http_url(tmp_path):
    path = download_emoji("x", "file:///etc/passwd", cache_dir=tmp_path)
    assert path is None


def test_download_emoji_rejects_disallowed_host(tmp_path):
    """白名单以外域名的下载请求应被直接拒绝（防恶意弹幕文件借道探测）。"""
    path = download_emoji("x", "https://internal.example.com/e.png", cache_dir=tmp_path)
    assert path is None


def test_download_emoji_blocks_private_ip(monkeypatch, tmp_path):
    """直连且域名解析到私网/环回地址时应阻止请求（SSRF 防护）。"""

    def fake_getaddrinfo(host, port, proto=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]

    monkeypatch.setattr(emoji_fetch.urllib.request, "getproxies", dict)
    monkeypatch.setattr(emoji_fetch.socket, "getaddrinfo", fake_getaddrinfo)
    path = download_emoji("x", "https://yt3.ggpht.com/e.png", cache_dir=tmp_path)
    assert path is None


def test_download_emoji_returns_none_on_error(monkeypatch, tmp_path):
    def fail_open(url, timeout):
        raise OSError("network down")

    monkeypatch.setattr(emoji_fetch, "_open_url", fail_open)

    path = download_emoji("x", "https://yt3.ggpht.com/e.png", cache_dir=tmp_path)
    assert path is None
