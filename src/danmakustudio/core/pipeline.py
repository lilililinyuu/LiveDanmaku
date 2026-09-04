"""pipeline 菜单工作台。

不再强制固定执行顺序：输入 URL（下载）或本地视频文件后进入交互菜单，
由用户自由选择要执行的步骤（下载 / JSON 转 XML / 配置摘要 / 预览图 /
参数展示 / 测试片段 / 完整压缩），可反复执行直至退出。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from ..config.models import DanmakuConfig, EncodeMode
from ..encode.ffmpeg import FFmpegManager
from ..errors import DanmakuStudioError
from ..input.chat2xml import convert_chat_json
from ..layout.engine import LayoutEngine
from ..utils.helpers import ensure_qt_app
from .burner import DanmakuBurner
from .preview import generate_previews

_YTDLP_FORMAT = "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b"
_VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".flv", ".mov", ".ts", ".avi")
_IMAGE_EXTS = (".webp", ".jpg", ".jpeg", ".png", ".avif", ".jfif", ".gif")


def _confirm(prompt: str, default: bool = False, yes: bool = False) -> bool:
    """交互确认；--yes 时直接通过。"""
    if yes:
        return True
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        answer = ""
    if not answer:
        return default
    return answer in ("y", "yes")


def parse_ytdlp_paths(lines: list[str]) -> tuple[Path | None, Path | None]:
    """从 yt-dlp --print after_move:filepath 输出中解析视频与弹幕 JSON 路径。

    弹幕 JSON 优先匹配 *.live_chat.json；若输出中只有普通 .json
    （例如弹幕文件与视频同名、仅后缀不同），则接受该 .json。
    """
    video: Path | None = None
    jsons: list[Path] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        path = Path(line)
        if path.suffix.lower() == ".json":
            jsons.append(path)
        elif (
            video is None
            and path.suffix.lower() in _VIDEO_EXTS
        ):
            video = path
    chat: Path | None = next(
        (p for p in jsons if ".live_chat" in p.name), None
    )
    if chat is None and len(jsons) == 1:
        chat = jsons[0]
    return video, chat


def download_stream(url: str, out_dir: Path, fmt: str) -> tuple[Path, Path]:
    """用 yt-dlp 下载直播回放视频与弹幕 JSON，返回 (视频路径, 弹幕 JSON 路径)。"""
    result = download_items(
        url, out_dir, fmt,
        want_video=True, want_chat=True, want_thumbnail=False,
    )
    video = result["video"]
    chat = result["chat"]
    assert video is not None and chat is not None
    return video, chat


def download_thumbnail(url: str, out_dir: Path) -> Path:
    """用 yt-dlp 下载视频封面，并用 ffmpeg 统一转为 PNG。"""
    result = download_items(
        url, out_dir, _YTDLP_FORMAT,
        want_video=False, want_chat=False, want_thumbnail=True,
    )
    assert result["thumbnail"] is not None
    return result["thumbnail"]


def download_items(
    url: str,
    out_dir: Path,
    fmt: str,
    *,
    want_video: bool,
    want_chat: bool,
    want_thumbnail: bool,
) -> dict[str, Path | None]:
    """按需下载视频 / 实时弹幕 JSON / 封面，三者可任意组合。

    封面会统一转为 PNG 并清理原始图片。返回
    {'video': Path|None, 'chat': Path|None, 'thumbnail': Path|None}，
    未选择的项为 None。
    """
    if not (want_video or want_chat or want_thumbnail):
        raise DanmakuStudioError("未选择任何要下载的内容。")
    if shutil.which("yt-dlp") is None:
        raise DanmakuStudioError(
            "未找到 yt-dlp，请先安装: pip install yt-dlp（或 winget install yt-dlp.yt-dlp）"
        )
    if want_thumbnail and shutil.which("ffmpeg") is None:
        raise DanmakuStudioError(
            "未找到 ffmpeg，无法把封面转换为 PNG，请先安装 ffmpeg。"
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    before = {p for p in out_dir.iterdir() if p.is_file()}

    cmd = ["yt-dlp", "--newline"]
    if want_video:
        cmd += ["-f", fmt]
    else:
        cmd += ["--skip-download"]
    if want_chat:
        cmd += ["--write-subs", "--sub-langs", "live_chat"]
    if want_thumbnail:
        cmd += ["--write-thumbnail"]
    cmd += [
        "-o", str(out_dir / "%(title)s.%(ext)s"),
        "--print", "after_move:filepath",
        url,
    ]

    logger.info("下载视频/弹幕/封面...")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        raise DanmakuStudioError(f"yt-dlp 下载失败: {result.stderr[-800:]}")

    video, chat = parse_ytdlp_paths(result.stdout.splitlines())

    thumbnail: Path | None = None
    if want_thumbnail:
        after = {p for p in out_dir.iterdir() if p.is_file()}
        thumbs = [p for p in (after - before) if p.suffix.lower() in _IMAGE_EXTS]
        if not thumbs:
            raise DanmakuStudioError("未找到下载的封面图片，请检查 yt-dlp 输出。")
        thumbnail = _largest_thumbnail_to_png(thumbs)

    if want_video and video is None:
        raise DanmakuStudioError("yt-dlp 输出中未找到视频文件，请检查下载结果。")
    if want_chat and chat is None:
        raise DanmakuStudioError("yt-dlp 输出中未找到弹幕 JSON，请检查下载结果。")

    return {"video": video, "chat": chat, "thumbnail": thumbnail}


def _largest_thumbnail_to_png(thumbs: list[Path]) -> Path:
    """从候选封面中取最大一张，统一转成 PNG 并清理其余原始图片。"""
    thumbs.sort(key=lambda p: p.stat().st_size, reverse=True)
    thumb = thumbs[0]
    if thumb.suffix.lower() == ".png":
        for extra in thumbs[1:]:
            extra.unlink(missing_ok=True)
        return thumb

    png_out = thumb.with_suffix(".png")
    conv = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(thumb), str(png_out),
    ]
    logger.info("封面转换为 PNG: {} -> {}", thumb.name, png_out.name)
    conv_result = subprocess.run(
        conv,
        capture_output=True,
        text=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if conv_result.returncode != 0 or not png_out.exists():
        raise DanmakuStudioError(
            f"封面转 PNG 失败: {conv_result.stderr[-400:] or '未生成输出文件'}"
        )
    # 清理所有下载的原始图片，只保留最终 PNG
    for p in thumbs:
        p.unlink(missing_ok=True)
    return png_out


# =============================================================================
# 工作台状态
# =============================================================================

@dataclass
class Workbench:
    """pipeline 工作台：保存各步骤的输入输出与配置。"""

    source: str                     # URL 或本地视频文件路径
    out_dir: Path
    config: DanmakuConfig
    encode_mode: EncodeMode = EncodeMode.AUTO
    lead_in: float = 0.0
    clip_duration: float = 60.0
    output: Path | None = None
    yes: bool = False
    fmt: str = _YTDLP_FORMAT

    video: Path | None = None       # 步骤1下载后填充
    chat_json: Path | None = None   # 步骤1下载后填充
    xml_path: Path | None = None    # 步骤2转换后填充


def find_sibling_json(video: Path) -> Path | None:
    """在视频同目录寻找弹幕 JSON：优先 *.live_chat.json，其次同名 .json。"""
    candidates = list(video.parent.glob(f"{video.stem}.live_chat.json"))
    if candidates:
        return candidates[0]
    candidates = list(video.parent.glob(f"{video.stem}.json"))
    if candidates:
        return candidates[0]
    return None


def resolve_source(wb: Workbench) -> None:
    """确定视频源：本地文件直接使用；URL 需先执行步骤 1。"""
    src = wb.source.strip()
    path = Path(src).expanduser()
    if path.is_file() and path.suffix.lower() in _VIDEO_EXTS:
        wb.video = path.resolve()
        wb.chat_json = find_sibling_json(wb.video)
    else:
        wb.video = None  # URL：等待步骤 1 下载


# =============================================================================
# 各步骤
# =============================================================================

def step_download(wb: Workbench) -> None:
    """[1] yt-dlp 下载直播回放视频 + 弹幕 JSON。"""
    src = wb.source.strip()
    path = Path(src).expanduser()
    if path.is_file() and path.suffix.lower() in _VIDEO_EXTS:
        print(f"已是本地视频文件，无需下载: {path}")
        return
    logger.info("下载直播回放...")
    video, chat = download_stream(src, wb.out_dir, wb.fmt)
    wb.video, wb.chat_json = video, chat
    logger.success(f"视频: {video}")
    logger.success(f"弹幕 JSON: {chat}")


def step_thumbnail(wb: Workbench) -> None:
    """[2] 下载视频封面并转为 PNG。"""
    src = wb.source.strip()
    path = Path(src).expanduser()
    if path.is_file():
        raise DanmakuStudioError("封面下载仅支持 URL 源，本地文件请直接截图。")
    thumb = download_thumbnail(src, wb.out_dir)
    logger.success(f"封面: {thumb}")


def step_convert(wb: Workbench) -> None:
    """[2] 弹幕 JSON → XML。"""
    if wb.video is None:
        raise DanmakuStudioError("尚无视频，请先执行步骤 1 下载（或直接指定本地视频文件）")
    chat = wb.chat_json or find_sibling_json(wb.video)
    if chat is None:
        raise DanmakuStudioError(f"未找到视频对应的弹幕 JSON: {wb.video.name}")
    xml_path = wb.video.with_suffix(".xml")
    logger.info("弹幕 JSON 转 XML...")
    stats = convert_chat_json(chat, xml_path, lead_in=wb.lead_in)
    wb.xml_path = xml_path
    logger.success(f"弹幕 XML: {xml_path}（{stats['messages']} 条弹幕）")


def _ensure_xml(wb: Workbench) -> Path:
    if wb.video is None:
        raise DanmakuStudioError("尚无视频，请先执行步骤 1 下载（或直接指定本地视频文件）")
    if wb.xml_path is None or not wb.xml_path.exists():
        xml_path = wb.video.with_suffix(".xml")
        if not xml_path.exists():
            raise DanmakuStudioError("弹幕 XML 不存在，请先执行步骤 2 转换")
        wb.xml_path = xml_path
    return wb.xml_path


def step_summary(wb: Workbench) -> None:
    """[3] 弹幕渲染配置摘要。"""
    style = wb.config.style
    print("━━━ 弹幕渲染配置摘要 ━━━")
    print(f"  字体: {style.font_family or '内置回退'} {style.font_size}pt")
    print(f"  普通文本: {style.text.color} 不透明度{style.text.opacity} "
          f"描边{style.text.outline_width or '无'} 阴影{style.text.shadow_opacity or '无'}")
    print(f"  会员文本: {style.member_text.color} 不透明度{style.member_text.opacity}")
    print(f"  表情: 大小{style.emoji.size_ratio}x 不透明度{style.emoji.opacity} 间距{style.emoji.spacing}")
    print(f"  首条弹幕: {wb.config.animation.first_danmaku_at if wb.config.animation.first_danmaku_at is not None else '字幕原时间轴'}")
    print(f"  编码: {wb.config.encode.cpu_preset}/{wb.config.encode.cpu_crf} (CPU) | "
          f"{wb.config.encode.qsv_preset}/q{wb.config.encode.qsv_quality} (QSV) | "
          f"自定义参数 {len(wb.config.encode.extra_args)} 条")
    print("━━━━━━━━━━━━━━━━━━━━━━")


def step_preview(wb: Workbench) -> None:
    """[4] 生成预览图。"""
    xml_path = _ensure_xml(wb)
    assert wb.video is not None
    preview_paths = generate_previews(
        str(wb.video), str(xml_path), wb.config,
        out_dir=wb.out_dir / "preview",
    )
    print("\n预览图已生成:")
    for p in preview_paths:
        print(f"  {p}")
    if _confirm("是否打开预览图查看？[y/N] ", default=False, yes=wb.yes):
        for p in preview_paths:
            os.startfile(str(p))


def describe_ffmpeg_command(wb: Workbench) -> list[str]:
    """构建实际将使用的 ffmpeg 命令（供展示确认）。"""
    assert wb.video is not None
    final_out = wb.output or (wb.video.parent / f"{wb.video.stem}-弹幕版.mp4")
    manager = FFmpegManager(
        str(wb.video), str(final_out), wb.encode_mode,
        wb.config.encode, wb.config.system,
    )
    v_info = manager.probe_video(str(wb.video))
    fps = float(v_info["fps"])
    w = int(v_info["w"])
    h = int(v_info["h"])
    _, layer_params = LayoutEngine.calculate_params(w, h, wb.config.style, wb.config.ratio)
    return manager.build_command(fps, w, h, layer_params)


def step_show_cmd(wb: Workbench) -> None:
    """[5] 展示最终 ffmpeg 压缩命令。"""
    _ensure_xml(wb)
    cmd = describe_ffmpeg_command(wb)
    print("\n将使用以下 ffmpeg 命令:")
    print("  " + subprocess.list2cmdline(cmd))
    if not _confirm(
        "压缩参数确认无误？回车继续（可用 --ffmpeg-args 调整）[Y/n] ",
        default=True, yes=wb.yes,
    ):
        raise SystemExit(0)


def _make_burner(wb: Workbench, out_path: Path, clip=None) -> DanmakuBurner:
    assert wb.video is not None
    return DanmakuBurner(
        str(wb.video), str(_ensure_xml(wb)), str(out_path),
        encode_mode=wb.encode_mode, config=wb.config, force=True,
        clip=clip,
    )


def step_test_clip(wb: Workbench) -> None:
    """[6] 测试压缩片段。"""
    assert wb.video is not None
    clip_out = wb.video.parent / f"{wb.video.stem}.test-clip.mp4"
    logger.info(f"测试压缩 {wb.clip_duration:.0f} 秒片段...")
    _make_burner(wb, clip_out, clip=(None, wb.clip_duration)).run()
    logger.success(f"测试片段: {clip_out}")


def step_full_encode(wb: Workbench) -> None:
    """[7] 完整视频压缩。"""
    assert wb.video is not None
    final_out = wb.output or (wb.video.parent / f"{wb.video.stem}-弹幕版.mp4")
    logger.info("开始完整视频压缩...")
    _make_burner(wb, final_out).run()
    logger.success(f"完成: {final_out}")


# =============================================================================
# 菜单循环
# =============================================================================

_MENU_ITEMS: list[tuple[str, str]] = [
    ("1", "下载直播回放（yt-dlp，仅 URL 源）"),
    ("2", "下载视频封面（yt-dlp，转 PNG）"),
    ("3", "弹幕 JSON 转 XML"),
    ("4", "弹幕渲染配置摘要"),
    ("5", "生成预览图"),
    ("6", "展示 ffmpeg 压缩命令"),
    ("7", "测试压缩片段（先确认效果）"),
    ("8", "完整视频压缩"),
]


def run_workbench(wb: Workbench) -> None:
    """进入菜单循环，由用户自由选择执行步骤，直至退出。"""
    ensure_qt_app()
    resolve_source(wb)
    if wb.video is not None:
        print(f"视频源: {wb.video}")
        chat = wb.chat_json or find_sibling_json(wb.video)
        if chat:
            print(f"弹幕 JSON: {chat}")
        else:
            print("弹幕 JSON: 未找到（可执行步骤 2 前先手动放置同名 .json）")
        xml = wb.video.with_suffix(".xml")
        print(f"弹幕 XML: {'已存在' if xml.exists() else '不存在（执行步骤 2 生成）'}")
    else:
        print(f"URL 源: {wb.source}")
        print("提示: 需先执行步骤 1 下载，之后才能进行其它步骤")

    steps = {
        "1": step_download,
        "2": step_thumbnail,
        "3": step_convert,
        "4": step_summary,
        "5": step_preview,
        "6": step_show_cmd,
        "7": step_test_clip,
        "8": step_full_encode,
    }

    while True:
        print("\n━━━ 选择要执行的步骤（可执行多次，q 退出）━━━")
        for key, title in _MENU_ITEMS:
            print(f"  [{key}] {title}")
        try:
            choice = input("请输入步骤序号: ").strip().lower()
        except EOFError:
            break
        if not choice:
            continue
        if choice in ("q", "quit", "exit", "0"):
            print("退出工作台")
            break
        if choice not in steps:
            print(f"未知步骤: {choice}")
            continue
        try:
            steps[choice](wb)
            logger.success(f"步骤 {choice} 完成")
        except DanmakuStudioError as e:
            logger.error(f"步骤 {choice} 失败 [{e.category.value}]: {e}")
        except SystemExit:
            raise
        except Exception as e:
            from ..errors import handle_error
            handle_error(e, component="pipeline", operation=f"step_{choice}")
            logger.error(f"步骤 {choice} 失败: {e}")
