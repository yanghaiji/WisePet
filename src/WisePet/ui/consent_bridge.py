"""位置授权弹窗桥：worker 线程阻塞等待，弹窗在主线程显示。

agent 工具循环跑在 AI worker 线程里，而 Qt 弹窗必须在主线程。
桥用「Qt 信号自动排队 + threading.Event」把两侧接起来：
worker 调 ask() 阻塞 → 信号排到主线程弹窗 → 用户选择后 worker 醒来继续。
"""
from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)

#: worker 等用户点弹窗的最长时限：弹窗被忽略时不要把对话线程挂死
CONSENT_WAIT_S = 120.0


class LocationConsentBridge(QObject):
    """ask() 在任意线程可调，返回 True 表示用户授权获取位置。"""

    #: 参数是完成回调 callable(bool)：主线程弹窗结束后回调并唤醒等待方
    _requested = Signal(object)

    def __init__(self, asker, parent=None):
        super().__init__(parent)
        #: asker: 在主线程执行，返回 bool（True=允许）；由组装根注入
        self._asker = asker
        self._requested.connect(self._show_dialog)

    def ask(self) -> bool:
        box: dict = {}
        done = threading.Event()

        def finish(granted: bool) -> None:
            box["granted"] = bool(granted)
            done.set()

        self._requested.emit(finish)
        done.wait(timeout=CONSENT_WAIT_S)
        if not done.is_set():
            logger.info("location consent dialog timed out")
        return bool(box.get("granted"))

    def _show_dialog(self, finish) -> None:
        try:
            finish(bool(self._asker()))
        except Exception:  # noqa: BLE001 - 弹窗异常不能挂死 worker 线程
            logger.exception("location consent dialog failed")
            finish(False)
