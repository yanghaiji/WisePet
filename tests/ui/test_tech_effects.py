"""TechEffect 出场特效：spawn 各阶段绘制不崩、驱动到结束发 finished、出场比退场更长。"""
import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QImage, QPainter

from WisePet.ui.tech_effects import (CANVAS, DURATION_MS, FRAME_MS, SPAWN_DURATION_MS,
                                     TechEffect)


def _paint_frame(effect, t):
    """在离屏 ARGB 图上跑一遍绘制，验证不抛异常。"""
    img = QImage(CANVAS, CANVAS, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    painter = QPainter(img)
    try:
        if effect._mode == "spawn":
            effect._paint_spawn(painter, t)
        else:
            effect._paint_exit(painter, t)
    finally:
        painter.end()
    return img


@pytest.mark.parametrize("t", [0.0, 0.3, 0.7, 0.85, 1.0])
def should_paint_spawn_each_phase_without_error(qtbot, t):
    effect = TechEffect(QPoint(100, 100), mode="spawn")
    qtbot.addWidget(effect)

    img = _paint_frame(effect, t)

    assert img.width() == CANVAS
    assert img.hasAlphaChannel()


def should_emit_finished_when_spawn_elapsed(qtbot):
    """手动推进帧到超时长即发 finished，不依赖真实 33ms 计时器。"""
    effect = TechEffect(QPoint(100, 100), mode="spawn")
    qtbot.addWidget(effect)
    fired = []
    effect.finished.connect(lambda: fired.append(1))

    for _ in range(SPAWN_DURATION_MS // FRAME_MS + 2):
        effect._advance()

    assert fired


def should_use_longer_duration_for_spawn_than_exit(qtbot):
    """出场更炫所以更长；退场保持原时长。"""
    spawn = TechEffect(QPoint(100, 100), mode="spawn")
    exit_effect = TechEffect(QPoint(100, 100), mode="exit")
    qtbot.addWidget(spawn)
    qtbot.addWidget(exit_effect)

    assert spawn._duration == SPAWN_DURATION_MS
    assert exit_effect._duration == DURATION_MS
    assert SPAWN_DURATION_MS > DURATION_MS
