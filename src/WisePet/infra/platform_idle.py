"""系统空闲时长（秒）：让桌宠知道「人是不是离开了电脑」。

Windows 用 user32.GetLastInputInfo + GetTickCount（纯 ctypes，无新依赖）；
macOS / 无权限 / 调用失败一律返回 None，由调用方按 0 处理，**永不阻塞、永不抛**。
不采集按键内容、不记录鼠标轨迹，只取一个时间差。
"""
from __future__ import annotations

import ctypes
import sys


class _LastInputInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_ulong)]


def idle_seconds() -> float | None:
    if sys.platform != "win32":
        return None
    try:
        info = _LastInputInfo()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        elapsed_ms = ctypes.windll.kernel32.GetTickCount() - info.dwTime
        return max(0.0, float(elapsed_ms) / 1000.0)
    except (AttributeError, OSError, ValueError):
        return None
