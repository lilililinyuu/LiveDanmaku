"""命令行入口模块

用法: danmakustudio
     danmakustudio video.mp4 danmaku.xml
     danmakustudio video.mp4 lyrics.lrc
     danmakustudio source/5.flv source/5.xml --encode gpu --config ./danmakustudio.yaml -f
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault(
    "QT_LOGGING_RULES",
    "qt.qpa.fonts=false;qt.text.font.db=false",
)

import argparse
from pathlib import Path

from loguru import logger
from PySide6.QtWidgets import QApplication

from .config.loader import load_config
from .config.models import EncodeMode
from .core.burner import DanmakuBurner
from .errors import DanmakuStudioError, handle_error
from .logger_config import configure_logger
from .utils.helpers import ensure_qt_app


def preview_main() -> None:
    """预览图生成子命令：danmakustudio preview <video> <xml> [选项]"""
    parser = argparse.ArgumentParser(
        prog="danmakustudio preview",
        description="生成弹幕渲染预览图（在全量压制前确认样式效果）",
    )
    parser.add_argument("video", help="视频文件路径")
    parser.add_argument("subtitle", help="弹幕 XML/LRC 文件路径")
    parser.add_argument("-c", "--config", default=None, help="配置文件路径")
    parser.add_argument(
        "-t", "--times", default=None,
        help="预览时间点（秒，逗号分隔），默认自动选择 3 个",
    )
    parser.add_argument(
        "-o", "--out-dir", default=None,
        help="输出目录（默认视频目录下的 preview/）",
    )
    parser.add_argument(
        "--yes", action="store_true",
        help="自动确认，不询问是否打开预览图",
    )
    args = parser.parse_args()

    configure_logger()
    ensure_qt_app()
    try:
        config = load_config(args.config)
        times = None
        if args.times:
            times = [float(x) for x in args.times.split(",")]
        from .core.preview import generate_previews

        paths = generate_previews(
            args.video, args.subtitle, config,
            times=times,
            out_dir=Path(args.out_dir) if args.out_dir else None,
        )
    except DanmakuStudioError as e:
        logger.error(f"[{e.category.value}] {e}")
        raise SystemExit(1)
    except Exception as e:
        handle_error(e, component="cli", operation="preview")
        raise SystemExit(1)

    print("\n预览图已生成:")
    for p in paths:
        print(f"  {p}")
    if not args.yes:
        try:
            answer = input("是否打开预览图查看？[y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer in ("y", "yes"):
            for p in paths:
                os.startfile(str(p))


def pipeline_main() -> None:
    """pipeline 子命令：菜单工作台。

    danmakustudio pipeline <YouTube链接 或 本地视频文件> [选项]
    进入交互菜单自由选择执行步骤（下载/转XML/预览/测试片段/完整压缩）。
    """
    parser = argparse.ArgumentParser(
        prog="danmakustudio pipeline",
        description="弹幕压制工作台：下载直播回放 → 转XML → 预览 → 测试 → 全量压缩（菜单选择执行步骤）",
    )
    parser.add_argument("source", help="YouTube 直播回放链接，或本地视频文件路径")
    parser.add_argument("-c", "--config", default=None, help="配置文件路径")
    parser.add_argument("--out-dir", default=".", help="工作目录（下载与中间产物存放处）")
    parser.add_argument(
        "--encode",
        choices=[EncodeMode.AUTO, EncodeMode.GPU, EncodeMode.QSV, EncodeMode.CPU],
        default=EncodeMode.AUTO,
        help="编码模式",
    )
    parser.add_argument("--lead-in", type=float, default=0.0, help="第一条弹幕出现时间（秒）")
    parser.add_argument("--clip-duration", type=float, default=60.0, help="测试片段时长（秒）")
    parser.add_argument("-o", "--output", default=None, help="最终输出文件路径")
    parser.add_argument("--yes", action="store_true", help="自动确认（不询问打开预览等）")
    parser.add_argument("--format", default=None, help="yt-dlp 下载格式（默认优先 mp4）")
    parser.add_argument(
        "--ffmpeg-args", default=None,
        help="自定义 ffmpeg 参数（追加在命令末尾，可覆盖默认压缩参数）",
    )
    args = parser.parse_args()

    configure_logger()
    try:
        config = load_config(args.config)
        if args.ffmpeg_args:
            import shlex
            from dataclasses import replace
            config = replace(
                config,
                encode=replace(
                    config.encode,
                    extra_args=config.encode.extra_args + shlex.split(args.ffmpeg_args),
                ),
            )
        from .core.pipeline import Workbench, run_workbench

        wb = Workbench(
            source=args.source,
            out_dir=Path(args.out_dir),
            config=config,
            encode_mode=EncodeMode(args.encode),
            lead_in=args.lead_in,
            clip_duration=args.clip_duration,
            output=Path(args.output) if args.output else None,
            yes=args.yes,
            fmt=args.format or Workbench.fmt,
        )
        run_workbench(wb)
    except DanmakuStudioError as e:
        logger.error(f"[{e.category.value}] {e}")
        raise SystemExit(1)
    except Exception as e:
        handle_error(e, component="cli", operation="pipeline")
        raise SystemExit(1)


def main() -> None:
    """主入口函数"""
    if len(sys.argv) > 1 and sys.argv[1] == "preview":
        sys.argv.pop(1)
        preview_main()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "pipeline":
        sys.argv.pop(1)
        pipeline_main()
        return

    parser = argparse.ArgumentParser(
        prog="danmakustudio",
        description="抖音直播弹幕压制工具",
    )
    parser.add_argument("video", nargs="?", help="视频文件路径")
    parser.add_argument("subtitle", nargs="?", help="弹幕 XML/LRC 文件路径")
    parser.add_argument("-o", "--output", default=None, help="输出视频路径")
    parser.add_argument(
        "--encode",
        choices=[EncodeMode.AUTO, EncodeMode.GPU, EncodeMode.QSV, EncodeMode.CPU],
        default=EncodeMode.AUTO,
        help="编码模式",
    )
    parser.add_argument("-c", "--config", default=None, help="配置文件路径")
    parser.add_argument(
        "-f", "--force", action="store_true", default=False,
        help="强制覆盖已存在的输出文件",
    )
    parser.add_argument(
        "--ffmpeg-args", default=None,
        help="自定义 ffmpeg 参数（空格分隔，追加在命令末尾，可覆盖默认压缩参数），"
        '例如: --ffmpeg-args "-preset faster -crf 26 -maxrate 4M -bufsize 8M"',
    )
    parser.add_argument(
        "--test-clip", action="store_true",
        help="测试片段模式：只压制一个时间窗口，用于先确认效果",
    )
    parser.add_argument(
        "--clip-start", type=float, default=None,
        help="片段起始时间（秒，默认首条弹幕处）",
    )
    parser.add_argument(
        "--clip-duration", type=float, default=60.0,
        help="片段时长（秒，默认 60）",
    )
    parser.add_argument(
        "--gui", action="store_true", default=False,
        help="启动图形界面",
    )
    args = parser.parse_args()

    if args.gui or (args.video is None and args.subtitle is None):
        from .gui import main as gui_main

        gui_main()
        return

    if args.video is None or args.subtitle is None:
        parser.error("请同时提供视频文件和 XML/LRC 字幕文件，或直接运行 danmakustudio 打开图形界面")

    configure_logger()
    ensure_qt_app()
    # 捕获创建Burner时的异常，避免程序崩溃
    try:
        config = load_config(args.config)
        if args.ffmpeg_args:
            # CLI 自定义参数追加在 yaml extra_args 之后（ffmpeg 后出现的参数优先生效）
            import shlex
            from dataclasses import replace
            config = replace(
                config,
                encode=replace(
                    config.encode,
                    extra_args=config.encode.extra_args + shlex.split(args.ffmpeg_args),
                ),
            )
        video_out = args.output
        if video_out is None and args.test_clip:
            video_path = Path(args.video)
            video_out = str(video_path.parent / f"{video_path.stem}.test-clip.mp4")
        clip = (args.clip_start, args.clip_duration) if args.test_clip else None
        burner = DanmakuBurner(
            video_in=args.video, xml_in=args.subtitle,
            video_out=video_out, encode_mode=args.encode,
            config=config, force=args.force, clip=clip,
        )
    except DanmakuStudioError as e:
        logger.error(f"[{e.category.value}] {e}")
        raise SystemExit(1)
    except Exception as e:
        handle_error(e, component="cli", operation="create_burner")
        raise SystemExit(1)
    logger.info(f"开始处理: {args.video}")
    # 捕获处理Burner时的异常，避免程序崩溃
    try:
        burner.run()
    except DanmakuStudioError as e:
        logger.error(f"压制失败 [{e.category.value}]: {e}")
        raise SystemExit(1)
    except Exception as e:
        handle_error(e, component="cli", operation="run")
        raise SystemExit(1)
    finally:
        app = QApplication.instance()
        if app is not None:
            app.quit()
            app.deleteLater()


if __name__ == "__main__":
    main()
