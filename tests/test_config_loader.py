"""配置加载器测试。"""

from __future__ import annotations

import pytest

from danmakustudio.config.loader import load_config
from danmakustudio.config.models import DEFAULT_CONFIG
from danmakustudio.errors import ConfigError


def test_load_config_explicit_missing_path_raises(tmp_path):
    missing = tmp_path / "missing.yaml"

    with pytest.raises(ConfigError, match="不存在"):
        load_config(missing)


def test_load_config_rejects_non_mapping_top_level(tmp_path):
    config_path = tmp_path / "bad.yaml"
    config_path.write_text("- not\n- mapping\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="顶层"):
        load_config(config_path)


def test_load_config_rejects_non_mapping_section(tmp_path):
    config_path = tmp_path / "bad.yaml"
    config_path.write_text("encode: 1\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="encode"):
        load_config(config_path)


def test_load_config_null_section_falls_back_to_default(tmp_path):
    config_path = tmp_path / "empty_section.yaml"
    config_path.write_text("encode:\n", encoding="utf-8")

    config = load_config(config_path)

    assert config.encode == DEFAULT_CONFIG.encode


def test_load_config_validation_error_is_config_error(tmp_path):
    config_path = tmp_path / "invalid_value.yaml"
    config_path.write_text("system:\n  video_alignment: 3\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="video_alignment"):
        load_config(config_path)


def test_load_config_nested_text_styles(tmp_path):
    """嵌套 text/member_text 应完整解析。"""
    config_path = tmp_path / "nested.yaml"
    config_path.write_text(
        "style:\n"
        "  font_family: \"SimSun\"\n"
        "  text:\n"
        "    color: \"#FFFFFF\"\n"
        "    opacity: 0.7\n"
        "    outline_color: \"#246542\"\n"
        "    outline_width: 0.7\n"
        "    shadow_color: \"#000000\"\n"
        "    shadow_opacity: 0.5\n"
        "  member_text:\n"
        "    color: \"#45dd7f\"\n",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.style.font_family == "SimSun"
    assert config.style.text.color == "#FFFFFF"
    assert config.style.text.opacity == 0.7
    assert config.style.text.outline_color == "#246542"
    assert config.style.text.outline_width == 0.7
    assert config.style.text.shadow_color == "#000000"
    assert config.style.text.shadow_opacity == 0.5
    assert config.style.member_text.color == "#45dd7f"


def test_load_config_text_style_partial_keeps_defaults(tmp_path):
    """只写部分 TextStyle 字段时，其余字段回退内置默认样式。"""
    config_path = tmp_path / "partial.yaml"
    config_path.write_text("style:\n  text:\n    color: \"#FF0000\"\n", encoding="utf-8")

    config = load_config(config_path)

    assert config.style.text.color == "#FF0000"
    assert config.style.text.opacity == DEFAULT_CONFIG.style.text.opacity
    assert config.style.text.outline_width == DEFAULT_CONFIG.style.text.outline_width
    assert config.style.text.shadow_opacity == DEFAULT_CONFIG.style.text.shadow_opacity
    assert config.style.member_text.color == DEFAULT_CONFIG.style.member_text.color
    assert config.style.emoji == DEFAULT_CONFIG.style.emoji


def test_load_config_partial_style_keeps_defaults(tmp_path):
    """style 段只写部分字段时，其余字段回退内置默认样式。"""
    config_path = tmp_path / "partial_style.yaml"
    config_path.write_text("style:\n  danmaku_x: 5\n", encoding="utf-8")

    config = load_config(config_path)

    assert config.style.danmaku_x == 5
    assert config.style.font_size == DEFAULT_CONFIG.style.font_size
    assert config.style.font_family == DEFAULT_CONFIG.style.font_family
    assert config.style.text == DEFAULT_CONFIG.style.text


def test_load_config_text_style_validation_error(tmp_path):
    """TextStyle 校验失败应包装为 ConfigError。"""
    config_path = tmp_path / "bad_style.yaml"
    config_path.write_text("style:\n  text:\n    opacity: 1.5\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="opacity"):
        load_config(config_path)


def test_config_to_yaml_roundtrip(tmp_path):
    """config_to_yaml 输出应能被 load_config 读回且与原配置相等。"""
    from danmakustudio.config.loader import config_to_yaml

    text = config_to_yaml(DEFAULT_CONFIG)
    target = tmp_path / "dump.yaml"
    target.write_text(text, encoding="utf-8")

    loaded = load_config(target)

    assert loaded == DEFAULT_CONFIG
