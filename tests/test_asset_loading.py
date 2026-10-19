"""基于 XML 文件测试图片资源加载是否完整

解析 XML 弹幕文件，提取所有 Emoji 名称，
验证对应的 PNG 图片资源是否存在且可正常加载。

XML 路径可通过 --xml-path 参数或 DANMAKU_XML 环境变量指定，
文件不存在时自动 skip。
"""

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from livedanmaku.render.assets import load_image_assets

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
EMOJI_DIR = _PROJECT_ROOT / "assets" / "emoji"


def test_load_image_assets_scales_to_target_height(qapp, tmp_path):
    """图片应等比缩放到目标行高（表情大小比例依赖此行为）。"""
    source = QImage(40, 40, QImage.Format.Format_ARGB32)
    source.fill(Qt.GlobalColor.red)
    assert source.save(str(tmp_path / "test.png"))

    cache: dict[str, QImage] = {}
    load_image_assets(tmp_path, {"test"}, 20, cache, "emoji")

    assert "test" in cache
    assert cache["test"].height() == 20
    assert cache["test"].width() == 20


# Emoji图片测试
class TestEmojiAssets:
    def test_emoji_png_files_exist(self, emoji_names):
        assert len(emoji_names) > 0, "XML 中未找到任何 Emoji 引用"

        missing = []
        for name in sorted(emoji_names):
            png_path = EMOJI_DIR / f"{name}.png"
            if not png_path.exists():
                missing.append(name)

        if missing:
            pytest.fail(
                f"以下 Emoji 缺少对应 PNG 文件 ({len(missing)} 个):\n" +
                "\n".join(f"  - [{name}] → {EMOJI_DIR / f'{name}.png'}" for name in missing)
            )

    def test_emoji_png_can_load(self, qapp, emoji_names):
        assert len(emoji_names) > 0, "XML 中未找到任何 Emoji 引用"

        failed = []
        for name in sorted(emoji_names):
            png_path = EMOJI_DIR / f"{name}.png"
            img = QImage(str(png_path))
            if img.isNull():
                failed.append(name)

        if failed:
            pytest.fail(
                f"以下 Emoji 图片加载失败 ({len(failed)} 个):\n" +
                "\n".join(f"  - [{name}] → {EMOJI_DIR / f'{name}.png'}" for name in failed)
            )

    def test_emoji_png_has_valid_size(self, qapp, emoji_names):
        assert len(emoji_names) > 0, "XML 中未找到任何 Emoji 引用"

        invalid = []
        for name in sorted(emoji_names):
            png_path = EMOJI_DIR / f"{name}.png"
            img = QImage(str(png_path))
            if img.isNull():
                continue
            if img.width() <= 0 or img.height() <= 0:
                invalid.append(f"{name} ({img.width()}x{img.height()})")

        if invalid:
            pytest.fail(
                f"以下 Emoji 图片尺寸无效 ({len(invalid)} 个):\n" +
                "\n".join(f"  - {item}" for item in invalid)
            )


# XML文件完整性测试
class TestXmlFileIntegrity:
    def test_xml_file_exists(self, xml_path):
        assert xml_path.exists(), f"XML 文件不存在: {xml_path}"

    def test_xml_contains_danmaku_events(self, events):
        assert len(events) > 0, "XML 中未找到弹幕事件"


# 资产加载器集成测试
class TestAssetLoaderIntegration:
    def test_asset_loader_loads_xml_resources(self, qapp, events):
        from livedanmaku.render.assets import AssetLoader

        assert len(events) > 0, "parse_xml 未返回任何事件"

        loader = AssetLoader()
        loader.load_assets(events)

        emoji_count = len(loader.emoji_cache)
        assert emoji_count > 0, "AssetLoader 未缓存任何 Emoji"

        failed = [
            f"[{name}]" for name, img in loader.emoji_cache.items() if img.isNull()
        ]
        if failed:
            pytest.fail(
                f"以下图片资源加载失败 ({len(failed)}/{emoji_count}):\n" +
                "\n".join(f"  - {item}" for item in failed)
            )

    def test_asset_loader_downloads_remote_emoji(self, qapp, monkeypatch, tmp_path):
        """本地 assets 缺失的表情应按 <e> 提供的 URL 下载并进入缓存。"""
        from PySide6.QtCore import QBuffer, QIODevice
        from PySide6.QtGui import QImage

        from livedanmaku.config.models import EmojiStyle
        from livedanmaku.input.event import DanmakuEvent
        from livedanmaku.render import emoji_fetch
        from livedanmaku.render.assets import AssetLoader

        # 构造一张真实 PNG 作为"远程图片"
        png_img = QImage(40, 40, QImage.Format.Format_ARGB32)
        png_img.fill(0xFFFF0000)
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        assert png_img.save(buf, "PNG")
        png_bytes = bytes(buf.data())

        class _Resp:
            def __init__(self, data):
                self._data = data

            def read(self):
                return self._data

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        monkeypatch.setattr(
            emoji_fetch, "_open_url", lambda url, timeout: _Resp(png_bytes)
        )
        monkeypatch.setattr(
            "livedanmaku.render.emoji_fetch.emoji_cache_dir", lambda: tmp_path
        )

        loader = AssetLoader(emoji_style=EmojiStyle(size_ratio=0.5))
        events = [DanmakuEvent(time=1.0, user="u", text="你好[remote1]世界")]
        loader.load_assets(events, {"remote1": "https://yt3.ggpht.com/remote1.png"})

        assert "remote1" in loader.emoji_cache
        img = loader.emoji_cache["remote1"]
        assert not img.isNull()
        # size_ratio 0.5 → 缩放至行高一半
        assert img.height() == max(1, int(loader.line_height * 0.5))
        # 磁盘缓存已生成，第二次加载不再联网
        assert (tmp_path / "remote1.png").exists()
