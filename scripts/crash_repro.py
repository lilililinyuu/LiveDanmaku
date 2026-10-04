# -*- coding: utf-8 -*-
"""端到端验证背压：最密集弹幕窗口 30 秒（用户报告整机崩溃的场景）。"""
import os
import sys
import threading
import time

ROOT = r"D:\youtube-video-download\youtube-chat-export\danmaku\DanmakuStudio"
sys.path.insert(0, ROOT + r"\src")

import ctypes


def commit_gb():
    class S(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_uint), ("dwMemoryLoad", ctypes.c_uint),
            ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
            ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
            ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
            ("ullAvailExtendedVirtual", ctypes.c_uint64),
        ]
    s = S()
    s.dwLength = ctypes.sizeof(s)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
    return (s.ullTotalPageFile - s.ullAvailPageFile) / 2**30


from danmakustudio.config.loader import load_config
from danmakustudio.utils.helpers import ensure_qt_app
from danmakustudio.core.burner import DanmakuBurner

ensure_qt_app()

OUT = ROOT + r"\直播录制\crash_repro.mp4"
config = load_config(ROOT + r"\danmakustudio.yaml")
burner = DanmakuBurner(
    video_in=ROOT + r"\直播录制\喷嚏+播音员.mp4",
    xml_in=ROOT + r"\test.xml",
    video_out=OUT,
    encode_mode="cpu",   # 用 CPU 验证完整链路（QSV 停转场景由单测覆盖）
    config=config,
    force=True,
    clip=(0.0, 30.0),
)

peak_commit = [0.0]
stop = threading.Event()


def monitor():
    while not stop.is_set():
        peak_commit[0] = max(peak_commit[0], commit_gb())
        time.sleep(1)


t = threading.Thread(target=monitor, daemon=True)
t.start()

t0 = time.time()
burner.run()
print(f"压制完成，耗时 {time.time()-t0:.0f}s，峰值提交费用 {peak_commit[0]:.1f}GB（有界）")
