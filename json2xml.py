import argparse
import sys
from pathlib import Path

# GBK 控制台无法编码 emoji 时会崩溃，此处降级为替换字符
try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from danmakustudio.input.chat2xml import convert_chat_json  # noqa: E402

parser = argparse.ArgumentParser(description="把 YouTube 直播回放 JSON 转为弹幕 XML")
parser.add_argument("input", nargs="?", default="chat.json", help="输入 JSON 文件（默认 chat.json）")
parser.add_argument("output", nargs="?", default="video.xml", help="输出 XML 文件（默认 video.xml）")
parser.add_argument(
    "--lead-in", type=float, default=0.0,
    help="第一条弹幕相对视频开始的时间（秒，默认 0；视频开始后多久出现弹幕）",
)
args = parser.parse_args()

if args.lead_in < 0:
    print("❌ --lead-in 不能为负数")
    exit()

try:
    stats = convert_chat_json(args.input, args.output, lead_in=args.lead_in)
except ValueError as e:
    print(f"❌ {e}")
    exit()

print(f"📊 视频起始时间: {stats['start_time']}（首条弹幕于 {stats['lead_in']} 秒处出现）")
print(f"📊 共 {stats['messages']} 条弹幕")
print(f"✅ 生成 {args.output}")
