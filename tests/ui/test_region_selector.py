"""RegionSelector 冒烟：拖拽框选出正确矩形、Esc 取消、误点不算选区。"""
from PySide6.QtCore import QPoint, QRect, Qt

from WisePet.ui.region_selector import RegionSelector


def _make_selector(qtbot):
    from PySide6.QtGui import QPixmap
    selector = RegionSelector(QPixmap(800, 600))
    qtbot.addWidget(selector)
    selector.show()
    return selector


def _drag(selector, qtbot, x1, y1, x2, y2):
    qtbot.mousePress(selector, Qt.MouseButton.LeftButton, pos=QPoint(x1, y1))
    qtbot.mouseMove(selector, QPoint(x2, y2))
    qtbot.mouseRelease(selector, Qt.MouseButton.LeftButton, pos=QPoint(x2, y2))


def should_emit_selected_with_normalized_rect(qtbot):
    selector = _make_selector(qtbot)
    results = []
    selector.selected.connect(results.append)

    _drag(selector, qtbot, 600, 400, 100, 100)      # 反向拖也要归一化

    rect = results[0]
    # Qt 矩形端点含 1px 语义差，允许 ±1 容差
    assert abs(rect.x() - 100) <= 1 and abs(rect.y() - 100) <= 1
    assert abs(rect.width() - 500) <= 1 and abs(rect.height() - 300) <= 1
    assert not selector.isVisible()


def should_cancel_on_escape(qtbot):
    selector = _make_selector(qtbot)
    results = []
    cancelled = []
    selector.selected.connect(results.append)
    selector.cancelled.connect(lambda: cancelled.append(1))

    qtbot.keyPress(selector, Qt.Key.Key_Escape)

    assert results == [] and cancelled == [1]
    assert not selector.isVisible()


def should_treat_tiny_drag_as_cancel(qtbot):
    selector = _make_selector(qtbot)
    results = []
    cancelled = []
    selector.selected.connect(results.append)
    selector.cancelled.connect(lambda: cancelled.append(1))

    _drag(selector, qtbot, 100, 100, 104, 103)      # 小于最小选区：视为误点

    assert results == [] and cancelled == [1]
