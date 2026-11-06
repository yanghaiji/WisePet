"""EventBus → Qt Signal 桥：全项目唯一跨线程事件转投点（技术方案 §2 Qt 隔离策略）。

app 层在任意线程发布领域事件，本模块把事件经 Signal 的队列连接投递回主线程，
ui 组件只在主线程更新控件；组件侧统一通过 EventRouter 按事件类型分派。
"""
from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)


class QtEventBridge(QObject):
    """把进程内 EventBus 的所有事件转发为 Qt 信号。"""

    #: object 参数携带领域事件 dataclass 实例
    event = Signal(object)

    def __init__(self, bus, parent: QObject | None = None):
        super().__init__(parent)
        self._bus = bus
        bus.subscribe(None, self._on_event)

    def _on_event(self, domain_event: object) -> None:
        """可能由 worker 线程调用：emit 线程安全，接收方在主线程收。"""
        try:
            self.event.emit(domain_event)
        except RuntimeError:  # 桥已销毁（退出竞态）：丢弃事件，不导致崩溃
            logger.debug("event bridge destroyed, drop event %r", domain_event)


class EventRouter:
    """isinstance 分派表：把桥接信号路由到各类型事件的处理函数。"""

    def __init__(self, bridge: QtEventBridge):
        self._bridge = bridge          # 持有引用，防止桥被回收
        self._handlers: list[tuple[type, Callable[[object], None]]] = []
        bridge.event.connect(self._dispatch)

    def on(self, event_type: type, handler: Callable[[object], None]) -> None:
        """注册某类事件（含其子类）的处理函数。"""
        self._handlers.append((event_type, handler))

    def _dispatch(self, domain_event: object) -> None:
        for event_type, handler in self._handlers:
            if isinstance(domain_event, event_type):
                try:
                    handler(domain_event)
                except Exception:  # noqa: BLE001 - 单个处理器失败不影响其它订阅者
                    logger.exception("ui handler %r failed for %r", handler, domain_event)
