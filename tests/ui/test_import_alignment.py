"""AI 素材导入器的像素级校验：对齐、抠底、留色。

放 ui/ 是因为要读像素与 alpha 通道（integration 层铁律不 import PySide6，
那边只按 PNG 头与退出码验契约）。这里验的是"换素材后动画不抖"的根因。
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter

from tools.import_pet_images import (BASE_Y, SIZE, TARGET_H, _bbox, _cut_background,
                                     _has_alpha, _normalize)


def _sheet(size: int = 120) -> QImage:
    img = QImage(size, size, QImage.Format.Format_RGB32)
    img.fill(QColor("#FFFFFF"))
    return img


def _rect(img: QImage, x: int, y: int, w: int, h: int, color: str) -> None:
    p = QPainter(img)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawRect(x, y, w, h)
    p.end()


def should_report_no_alpha_on_opaque_sheet():
    assert _has_alpha(_sheet()) is False


def should_cut_border_background_but_keep_enclosed_same_color():
    """角色身上的底色块不许被抠穿：只有与边框连通的近底色像素才被吃掉。"""
    img = _sheet()
    _rect(img, 30, 30, 60, 60, "#000000")     # 外圈：挡住内部白色不被边框泛洪到达
    _rect(img, 45, 45, 30, 30, "#FFFFFF")     # 内芯：与背景同色但被包着

    cut = _cut_background(img, 40)
    box = _bbox(cut)

    assert box is not None
    assert (box[0], box[1]) == (30, 30) and (box[2], box[3]) == (89, 89)
    assert cut.pixelColor(60, 60).alpha() == 255      # 内芯保住了
    assert cut.pixelColor(5, 5).alpha() == 0          # 四角背景被抠掉


def should_anchor_feet_and_center_when_content_offsets_differ():
    """同一动作的两帧画在网格不同位置、大小不一 → 归一后必须同脚线同中心同身高。"""
    anchors = []
    for x, y, side in ((10, 70, 24), (46, 20, 40)):
        img = _sheet()
        _rect(img, x, y, side, side, "#3C7A3C")

        frame = _normalize(_cut_background(img, 40), f"帧@{x},{y}")
        box = _bbox(frame)
        assert box is not None
        anchors.append((box[3], round((box[0] + box[2]) / 2), box[3] - box[1]))

    for bottom, center_x, _ in anchors:
        assert abs(bottom - BASE_Y) <= 1               # 脚底贴同一条地平线
        assert abs(center_x - SIZE / 2) <= 1           # 水平居中
    assert abs(anchors[0][2] - anchors[1][2]) <= 2     # 身高被拉平，不是一帧大一幅小


def should_fill_target_height_when_content_is_tall():
    img = _sheet(200)
    _rect(img, 80, 20, 40, 160, "#3C7A3C")

    box = _bbox(_normalize(_cut_background(img, 40), "竖长条"))

    assert box is not None
    assert abs((box[3] - box[1]) - TARGET_H) <= 2


def should_keep_within_canvas_when_content_is_wide():
    """横躺姿态（伸懒腰那类）不许顶破画布，否则窗口里会被裁掉一块。"""
    img = _sheet(200)
    _rect(img, 10, 90, 180, 30, "#3C7A3C")

    box = _bbox(_normalize(_cut_background(img, 40), "横长条"))

    assert box is not None
    assert box[0] >= 0 and box[2] < SIZE


def should_raise_when_frame_is_empty_after_cut():
    """整格都是背景色：抠完没内容，宁可报错也不要静默写一张全透明的帧。"""
    with pytest.raises(ValueError, match="透明的"):
        _normalize(_cut_background(_sheet(), 40), "空格子")
