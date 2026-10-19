"""YouTube 表情图片的运行时下载与磁盘缓存。

表情名 -> 图片 URL 由弹幕 XML 中的 <e name url/> 标签提供
（由 json2xml.py 从 YouTube 直播回放 JSON 提取）。
首次下载后缓存到用户目录，后续压制离线可用。
"""

from __future__ import annotations

import ipaddress
import socket
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from loguru import logger

_DEFAULT_TIMEOUT = 15.0

# 表情图片 URL 来自第三方弹幕文件，不可信：仅允许 YouTube 系图床域名，
# 防止恶意弹幕文件借表情下载探测内网（SSRF）
_ALLOWED_IMAGE_HOSTS = (
    "ytimg.com",
    "ggpht.com",
    "googleusercontent.com",
    "youtube.com",
)


def _assert_public_url(url: str) -> None:
    """校验下载地址：必须为 https、图床白名单域名，且（直连时）解析到公网 IP。"""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"仅允许 https 链接: {url}")
    host = (parsed.hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in _ALLOWED_IMAGE_HOSTS):
        raise ValueError(f"域名不在允许的图床白名单内: {host}")
    # 走系统代理时由代理解析域名并出站，本地解析结果不具代表性
    #（污染 DNS 会把图床域名解析成假地址造成误报），跳过 IP 侧检查
    if urllib.request.getproxies():
        return
    for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError(f"{host} 解析到内网/保留地址 {ip}，已阻止请求")


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """重定向目标逐跳复检，防止白名单域名 30x 跳转到内网。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _assert_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_url(url: str, timeout: float):
    """发起（已校验的）下载请求；独立成函数便于测试替换。"""
    opener = urllib.request.build_opener(_SafeRedirectHandler)
    return opener.open(url, timeout=timeout)


def emoji_cache_dir() -> Path:
    """表情图片的磁盘缓存目录（用户目录）。"""
    return Path.home() / ".livedanmaku_emoji_cache"


def download_emoji(name: str, url: str, cache_dir: Path | None = None) -> Path | None:
    """下载表情图片到磁盘缓存，返回文件路径；失败返回 None。

    已缓存时直接返回缓存路径，不再发起网络请求。
    """
    directory = cache_dir or emoji_cache_dir()
    target = directory / f"{name}.png"
    if target.exists():
        return target
    try:
        _assert_public_url(url)
    except ValueError as e:
        logger.warning(f"表情 URL 校验未通过，跳过下载 [{name}]: {e}")
        return None
    try:
        with _open_url(url, _DEFAULT_TIMEOUT) as resp:
            data = resp.read()
        directory.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    except (OSError, ValueError) as e:
        logger.warning(f"表情下载失败 [{name}]: {e}")
        return None
    return target
