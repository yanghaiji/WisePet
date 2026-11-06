"""平台差异收敛：全屏检测（勿扰）与工作区几何工具（技术方案 D5）。"""
from __future__ import annotations

import ctypes
import logging
import sys

from ..domain.timeutil import is_in_night  # 供 infra/app 内部使用

logger = logging.getLogger(__name__)

#: 全屏判定容差（像素），部分平台窗口会外扩/内缩若干像素
_FULL_SCREEN_TOLERANCE = 4

Rect = tuple  # (x1, y1, x2, y2)


def rect_covers_workarea(window_rect: Rect, work_area: Rect) -> bool:
    """窗口矩形是否覆盖整个工作区（近似全屏判定，纯函数便于测试）。"""
    wx1, wy1, wx2, wy2 = work_area
    x1, y1, x2, y2 = window_rect
    return (x1 <= wx1 + _FULL_SCREEN_TOLERANCE
            and y1 <= wy1 + _FULL_SCREEN_TOLERANCE
            and x2 >= wx2 - _FULL_SCREEN_TOLERANCE
            and y2 >= wy2 - _FULL_SCREEN_TOLERANCE)


class NullFullscreenDetector:
    """无法/不检测全屏的平台实现（macOS MVP：恒 False，PRD 已接受降级）。"""

    def is_user_fullscreen(self) -> bool:
        return False


class WindowsFullscreenDetector:
    """ctypes 前台窗口 vs 工作区比对；任何 API 失败静默返回 False。

    最大化（IsZoomed）不算全屏：最大化写代码/看网页是常态，提醒不该被静音；
    勿扰对象是游戏/视频那类无边框覆盖全工作区的窗口（PRD M6 意图）。
    """

    _SPI_GETWORKAREA = 0x0030

    def is_user_fullscreen(self) -> bool:
        if sys.platform != "win32":
            return False
        try:
            from ctypes import wintypes  # 惰性导入：非 Windows 平台不可用

            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return False
            if user32.IsZoomed(hwnd):
                return False             # 最大化窗口：照常提醒
            win_rect = wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(win_rect)):
                return False
            work_rect = wintypes.RECT()
            if not user32.SystemParametersInfoW(self._SPI_GETWORKAREA, 0,
                                                ctypes.byref(work_rect), 0):
                return False
            return rect_covers_workarea(
                (win_rect.left, win_rect.top, win_rect.right, win_rect.bottom),
                (work_rect.left, work_rect.top, work_rect.right, work_rect.bottom),
            )
        except Exception:  # noqa: BLE001 - 平台 API 尽力而为，失败不打扰
            logger.debug("fullscreen detection failed", exc_info=True)
            return False


def create_fullscreen_detector():
    """按运行平台装配检测器（main.py 组装根调用）。"""
    if sys.platform == "win32":
        return WindowsFullscreenDetector()
    return NullFullscreenDetector()
