"""布局参数

布局计算所需的数据结构。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LayerParams:
    """渲染层参数"""
    layer_x: int
    layer_y: int
    layer_w: int
    layer_h: int
