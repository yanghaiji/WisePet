"""进程内事件总线：同步 pub-sub，监听器异常隔离（技术方案 §3.5）。"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Callable

logger = logging.getLogger(__name__)

Handler = Callable[[object], None]
_ALL = object()  # 订阅所有事件类型的通配键


class EventBus:
    def __init__(self):
        self._handlers: dict[object, list[Handler]] = defaultdict(list)

    def subscribe(self, event_type: type | None, handler: Handler) -> None:
        """event_type=None 表示订阅所有类型（UI 适配器/测试收集用）。"""
        self._handlers[event_type if event_type is not None else _ALL].append(handler)

    def publish(self, event) -> None:
        handlers = list(self._handlers.get(type(event), ()))
        handlers += list(self._handlers.get(_ALL, ()))
        for handler in handlers:
            try:
                handler(event)
            except Exception:  # noqa: BLE001 - 单个监听器失败不影响其他
                logger.exception("event handler %r failed for %r", handler, event)
