"""虚拟桌面工作区读取与变化监听（技术方案 D4 / AGENT.md 规约 6）。

坐标一律基于 `QGuiApplication.primaryScreen().virtualGeometry()`，
窗口/气泡/输入条共用同一个 bounds 来源，避免各自取屏幕导致多屏不一致。
"""
from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtGui import QGuiApplication

from ..domain.position import Bounds

logger = logging.getLogger(__name__)

#: 无屏幕信息时的兜底范围（等价于不钳制），保证单测/异常场景不崩
UNBOUNDED: Bounds = (-10 ** 9, -10 ** 9, 10 ** 9, 10 ** 9)


def fit_beside_width(geometry: tuple[int, int, int, int], pet_rect,
                     width: int, min_width: int = 300, gap: int = 12) -> int:
    """按两侧可用空间收缩浮窗宽度，保证至少一侧能完整停靠（FR v1.10 C17）。

    宠物被拖到屏幕中间时两侧都可能容不下 440px 的历史窗；先缩到较宽那侧放得下，
    再由 beside_position 选边，就不会出现「clamp 回屏幕内但仍压住宠物」。
    """
    x1, _y1, x2, _y2 = geometry
    left_room = pet_rect.left() - x1 - gap
    right_room = x2 - pet_rect.right() - gap
    return max(min_width, min(width, max(left_room, right_room)))


def beside_position(geometry: tuple[int, int, int, int], pet_rect,
                    width: int, height: int, gap: int = 12) -> tuple[int, int]:
    """聊天浮窗贴宠物放：**左侧优先**，左侧放不下才放右侧（FR v1.10 C17）。

    纯函数，便于 ScreenBounds 与测试替身共用同一套算法。pet_rect 为宠物全局矩形
    （QRect，含端点）；垂直方向尽量与宠物居中，最后交给 clamp 收进工作区。
    """
    x1, y1, x2, y2 = geometry
    left_x = pet_rect.left() - width - gap
    right_x = pet_rect.right() + 1 + gap
    y = pet_rect.center().y() - height // 2
    if left_x >= x1:
        x = left_x
    elif right_x + width - 1 <= x2:
        x = right_x
    else:
        x = left_x if (pet_rect.left() - x1) >= (x2 - pet_rect.right()) else right_x
    min_x, min_y, max_x, max_y = (x1, y1, max(x1, x2 - width + 1),
                                  max(y1, y2 - height + 1))
    return (max(min_x, min(max_x, x)), max(min_y, min(max_y, y)))


class ScreenBounds:
    """工作区几何读取器。"""

    def geometry(self) -> tuple[int, int, int, int]:
        """(x1, y1, x2, y2)：所有屏幕合成的虚拟桌面矩形，右下含端点。"""
        screen = self._screen()
        if screen is None:
            return UNBOUNDED
        geo = screen.virtualGeometry()
        return (geo.x(), geo.y(),
                geo.x() + geo.width() - 1, geo.y() + geo.height() - 1)

    def bounds_for(self, width: int, height: int) -> Bounds:
        """窗口左上角可活动范围：虚拟桌面减去自身尺寸（多屏自然覆盖副屏）。"""
        x1, y1, x2, y2 = self.geometry()
        if (x1, y1, x2, y2) == UNBOUNDED:
            return UNBOUNDED
        return (x1, y1, max(x1, x2 - width + 1), max(y1, y2 - height + 1))

    def clamp(self, x: int, y: int, width: int, height: int) -> tuple[int, int]:
        """把控件左上角坐标钳制到工作区内（分辨率变化后重算）。"""
        min_x, min_y, max_x, max_y = self.bounds_for(width, height)
        return (max(min_x, min(max_x, x)), max(min_y, min(max_y, y)))

    def place_beside(self, pet_rect, width: int, height: int,
                     gap: int = 12) -> tuple[int, int]:
        """聊天浮窗贴着宠物放：**左侧优先**，左侧放不下才放右侧（FR v1.10 C17）。

        pet_rect 是宠物窗口的全局矩形（QRect，含端点）；垂直方向尽量与宠物居中，
        最后统一 clamp 回工作区。两侧都放不下时选剩余空间更大的一侧再 clamp——
        物理上没空间时无法完全避开宠物，但至少不会跑到屏幕外。
        """
        return beside_position(self.geometry(), pet_rect, width, height, gap)

    def watch_changes(self, callback: Callable[[], None]) -> None:
        """分辨率/插拔显示器变化时回调（PRD M1 边界：位置重新钳制）。"""
        screen = self._screen()
        if screen is not None:
            screen.geometryChanged.connect(lambda *_: callback())
        else:
            logger.debug("no screen to watch geometry changes")

    @staticmethod
    def _screen():
        app = QGuiApplication.instance()
        if app is None:
            logger.warning("no QGuiApplication instance, bounds disabled")
            return None
        screen = app.primaryScreen()
        if screen is None:
            logger.warning("no primary screen available, bounds disabled")
        return screen
