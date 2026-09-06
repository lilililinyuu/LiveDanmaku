"""资源加载器

管理字体和 Emoji 图片的加载与缓存。
"""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger
from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QFont,
    QFontDatabase,
    QFontMetrics,
    QGuiApplication,
    QImage,
    QRawFont,
)

from ..config.models import DEFAULT_CONFIG
from ..input.event import DanmakuEvent
from ..utils import extract_emoji_names


def _resource_root() -> Path:
    """返回资源根目录，兼容源码运行和 PyInstaller。"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent.parent.parent


_RESOURCE_ROOT = _resource_root()

# 核心字体族：按优先级排列，所有 Windows 10+ 系统均自带
_CORE_FAMILIES: list[str] = [
    "Microsoft YaHei",
    "Segoe UI",
    "Segoe UI Emoji",
    "Segoe UI Symbol",
]


def load_image_assets(
    asset_dir: Path,
    asset_names: set[str],
    line_height: int,
    cache: dict[str, QImage],
    asset_type: str,
) -> None:
    """加载图片资源到缓存字典（独立函数，供测试等场景使用）。

    Args:
        asset_dir: 图片资源目录
        asset_names: 需要加载的图片名称集合（不含扩展名）
        line_height: 目标行高，图片将等比缩放至此高度
        cache: 目标缓存字典，key 为名称，value 为缩放后的 QImage
        asset_type: 资源类型描述（用于日志）
    """
    missing: set[str] = set()
    if not asset_dir.exists():
        logger.warning(f"{asset_type} 文件夹不存在")
        return

    for name in asset_names:
        file_path = asset_dir / f"{name}.png"
        img = QImage(str(file_path))
        if not img.isNull():
            cache[name] = img.scaled(
                line_height, line_height,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        else:
            missing.add(name)

    if missing:
        logger.warning(f"{asset_type} 缺失图片: {sorted(missing)}")


class AssetLoader:
    """资源加载器"""

    def __init__(
        self,
        font_size: int = DEFAULT_CONFIG.style.font_size,
        font_family: str = DEFAULT_CONFIG.style.font_family,
        emoji_style=DEFAULT_CONFIG.style.emoji,
        render_scale: float = 1.0,
    ):
        self.emoji_cache: dict[str, QImage] = {}
        self._font_size = font_size
        self._font_family = font_family
        self._emoji_style = emoji_style
        self.render_scale = render_scale   # 超采样倍率：表情按倍率加载保持清晰

        if QGuiApplication.instance() is None:
            raise RuntimeError("必须先创建 QApplication")

        self._loaded_families: list[str] = []
        self._init_fonts()

        self.emoji_dir = _RESOURCE_ROOT / "assets" / "emoji"

    def _init_fonts(self) -> None:
        """初始化字体族：用户指定字体置于回退链最前。"""
        if self._font_family:
            self._loaded_families = [self._font_family, *_CORE_FAMILIES]
        else:
            self._loaded_families = list(_CORE_FAMILIES)
        self._rebuild_font()

    def _rebuild_font(self) -> None:
        """重建字体"""
        self.font = QFont()
        self.font.setFamilies(self._loaded_families)
        self.font.setPointSize(self._font_size)
        self.font.setBold(True)
        self.font.setStyleStrategy(QFont.StyleStrategy.PreferQuality)
        self.fm = QFontMetrics(self.font)
        self.line_height = self.fm.height()

    def load_assets(
        self,
        events: list[DanmakuEvent],
        remote_emoji: dict[str, str] | None = None,
    ) -> None:
        """按需加载资源。

        Args:
            events: 弹幕事件列表
            remote_emoji: 表情名 -> 图片 URL 映射（来自 XML 的 <e> 标签），
                本地 assets 中不存在的表情会按 URL 下载并缓存
        """
        used_emoji: set[str] = set()
        all_chars: set[str] = set()

        for ev in events:
            all_chars.update(ev.user)
            all_chars.update(ev.text)
            if '[' in ev.text and ']' in ev.text:
                for name in extract_emoji_names(ev.text):
                    used_emoji.add(name)

        self._load_fonts_for_chars(all_chars)
        emoji_target_height = max(
            1, int(self.line_height * self._emoji_style.size_ratio * self.render_scale)
        )
        self._load_image_assets(
            self.emoji_dir, used_emoji, self.emoji_cache, "Emoji",
            target_height=emoji_target_height,
        )
        if remote_emoji:
            self._load_remote_emoji(used_emoji, remote_emoji, emoji_target_height)

    def _load_remote_emoji(
        self,
        used_emoji: set[str],
        remote_emoji: dict[str, str],
        target_height: int,
    ) -> None:
        """下载本地 assets 中没有的远程表情图片。"""
        from .emoji_fetch import download_emoji

        pending = {name for name in used_emoji if name not in self.emoji_cache}
        for name in sorted(pending):
            url = remote_emoji.get(name)
            if not url:
                continue
            path = download_emoji(name, url)
            if path is None:
                continue
            load_image_assets(
                path.parent, {name}, target_height, self.emoji_cache, "远程Emoji",
            )
        missing = {n for n in pending if n not in self.emoji_cache}
        if missing:
            logger.warning(f"远程表情不可用，将以文字显示: {sorted(missing)}")

    def _load_image_assets(
        self,
        asset_dir: Path,
        asset_names: set[str],
        cache: dict[str, QImage],
        asset_type: str,
        target_height: int | None = None,
    ) -> None:
        """加载图片资源，委托给模块级函数。"""
        load_image_assets(asset_dir, asset_names, target_height or self.line_height, cache, asset_type)

    def _load_fonts_for_chars(self, chars: set[str]) -> None:
        """按需从系统字体库加载覆盖缺失字符的字体"""
        raw_fonts = self._build_raw_fonts()
        missing = self._find_missing_chars(chars, raw_fonts)
        if not missing:
            return

        newly_loaded = 0
        db = QFontDatabase()
        for family in db.families():
            if family in self._loaded_families:
                continue
            raw = self._build_single_raw_font(family)
            if any(self._raw_font_has_char(raw, c) for c in missing):
                self._loaded_families.append(family)
                newly_loaded += 1
                missing = {c for c in missing if not self._raw_font_has_char(raw, c)}
                if not missing:
                    break

        if newly_loaded > 0:
            self._rebuild_font()

        if missing:
            logger.warning(
                f"以下字符无字体覆盖，将显示为占位符: {''.join(sorted(missing))}"
            )

    def _build_raw_fonts(self) -> list[QRawFont]:
        """构建已加载字体的 QRawFont 列表"""
        raw_fonts = []
        for family in self._loaded_families:
            raw_fonts.append(self._build_single_raw_font(family))
        return raw_fonts

    def _build_single_raw_font(self, family: str) -> QRawFont:
        """构建单个字体的 QRawFont"""
        f = QFont(family, self._font_size, QFont.Weight.Bold)
        return QRawFont.fromFont(f)

    def _find_missing_chars(self, chars: set[str], raw_fonts: list[QRawFont]) -> set[str]:
        """查找缺失字符"""
        missing: set[str] = set()
        for c in chars:
            if c == ' ':
                continue
            for rf in raw_fonts:
                if self._raw_font_has_char(rf, c):
                    break
            else:
                missing.add(c)
        return missing

    @staticmethod
    def _raw_font_has_char(rf: QRawFont, char: str) -> bool:
        """检查 QRawFont 是否包含指定字符的字形"""
        indexes = rf.glyphIndexesForString(char)
        return len(indexes) > 0 and indexes[0] != 0
