"""频道开播监控。

轮询探测指定频道（或单个直播视频 URL）是否正在直播：
- 频道主页自动指向 /live 探测页；
- 探测命令通过 --print 输出 id|is_live|live_status|title，解析判定；
- 探测到开播时返回直播视频 URL，由界面自动启动录制。
"""

from __future__ import annotations

import subprocess

from loguru import logger

from .recorder import normalize_stream_url

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def probe_channel_live(
    channel_url: str,
    *,
    proxy: str = "",
    pot_token_script: str = "",
    timeout: float = 90.0,
) -> tuple[str | None, str]:
    """探测频道是否正在直播。

    返回 (直播视频 URL, 标题)；未开播或探测失败返回 (None, "")。
    """
    probe_url = normalize_stream_url(channel_url)
    cmd = ["yt-dlp", "--skip-download", "--print", "%(id)s|%(is_live)s|%(live_status)s|%(title).80s"]
    if proxy:
        cmd += ["--proxy", proxy]
    if pot_token_script:
        cmd += [
            "--extractor-args",
            f"youtubepot-bgutilscript:script_path={pot_token_script}",
        ]
    cmd.append(probe_url)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            creationflags=_CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        logger.warning("频道探测超时: {}", probe_url)
        return None, ""

    if result.returncode != 0:
        # 频道未开播时 /live 页可能解析失败，属正常情况
        logger.debug("频道探测未命中直播: {} ({})", probe_url, result.stderr[-200:])
        return None, ""

    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|", 3)
        if len(parts) != 4:
            continue
        video_id, is_live, live_status, title = parts
        if is_live == "True" or live_status == "is_live":
            return f"https://www.youtube.com/watch?v={video_id}", title
    return None, ""
