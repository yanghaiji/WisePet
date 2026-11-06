"""单实例互斥：回环端口占用检测（跨平台一致，AGENT.md 决策表）。"""
from __future__ import annotations

import logging
import socket

logger = logging.getLogger(__name__)

DEFAULT_PORT = 45789
_BIND_HOST = "127.0.0.1"


class InstanceGuard:
    """acquire 成功后必须持有引用直至进程退出，release 时显式释放。"""

    def __init__(self, port: int = DEFAULT_PORT):
        self._requested_port = port
        self._sock: socket.socket | None = None
        self.port: int | None = None

    def acquire(self) -> bool:
        if self._sock is not None:
            return True
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind((_BIND_HOST, self._requested_port))
            sock.listen(1)
        except OSError as exc:
            sock.close()
            logger.info("another instance is running (%s), exiting", exc)
            return False
        self._sock = sock
        self.port = sock.getsockname()[1]
        return True

    def release(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None
            self.port = None
