"""全局快捷键（仅 Windows）：Ctrl+Alt+P 随时唤起聊天，不用去找猫。

RegisterHotKey + 独立消息循环线程；任何失败静默降级（不注册就不生效，不影响主程序）。
回调在热键线程触发，由调用方经 EventBus 转投主线程。
"""
from __future__ import annotations

import logging
import sys
import threading

logger = logging.getLogger(__name__)

_HOTKEY_ID = 0xB16B
_MOD_CONTROL_ALT = 0x0002 | 0x0004      # MOD_CONTROL | MOD_ALT
_VK_P = 0x50


class GlobalHotKey:
    """注册一个全局热键（当前固定 Ctrl+Alt+P）；非 Windows 或失败时为空操作。"""

    def __init__(self, callback):
        self._callback = callback
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None

    @staticmethod
    def available() -> bool:
        return sys.platform == "win32"

    def start(self) -> None:
        if not self.available() or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="wisepet-hotkey",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread_id is None:
            return
        try:
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(
                self._thread_id, 0x0012, 0, 0)   # WM_QUIT
        except Exception:  # noqa: BLE001 - 退出路径尽力而为
            logger.debug("hotkey stop failed", exc_info=True)
        self._thread = None
        self._thread_id = None

    def _loop(self) -> None:
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            user32.RegisterHotKey.restype = wintypes.BOOL
            if not user32.RegisterHotKey(None, _HOTKEY_ID, _MOD_CONTROL_ALT, _VK_P):
                logger.warning("global hotkey register failed（可能被其它程序占用）")
                return
            self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
            logger.info("global hotkey registered: Ctrl+Alt+P")
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == 0x0312 and msg.wParam == _HOTKEY_ID:  # WM_HOTKEY
                    try:
                        self._callback()
                    except Exception:  # noqa: BLE001 - 热键线程兜底
                        logger.exception("hotkey callback failed")
            user32.UnregisterHotKey(None, _HOTKEY_ID)
        except Exception:  # noqa: BLE001 - 平台 API 尽力而为
            logger.warning("global hotkey unavailable", exc_info=True)
