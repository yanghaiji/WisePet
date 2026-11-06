"""AI 请求 worker：QThread 内串行消费 ChatService 队列（技术方案 D1）。

单线程天然满足 PRD M4「请求排队、不并发打请求」；httpx 同步流在本线程内阻塞，
结果事件经 EventBus → QtEventBridge 转投主线程，本线程绝不触碰任何控件。
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QThread

logger = logging.getLogger(__name__)

#: 队列空闲时的轮询间隔：避免空转吃 CPU，同时无需跨线程条件变量（PRD NFR CPU<3%）
IDLE_POLL_MS = 100


class AiWorker(QThread):
    """常驻消费线程：由 main.py 启动并在 aboutToQuit 取消回收。"""

    def __init__(self, chat_service, parent: QThread | None = None):
        super().__init__(parent)
        self._chat = chat_service

    def run(self) -> None:  # noqa: D102 - QThread 重写
        while not self.isInterruptionRequested():
            try:
                handled = self._chat.process_next()
            except Exception:  # noqa: BLE001 - 消费异常绝不让线程退出（PRD 可靠性）
                logger.exception("chat consumption failed")
                handled = False
            if not handled:
                self.msleep(IDLE_POLL_MS)
        logger.debug("ai worker thread stopped")

    def cancel(self) -> None:
        """取消在途请求并退出线程（PRD M4 异常：进程退出时未完成请求取消）。"""
        self._chat.cancel_inflight()
        self.requestInterruption()
