"""系统时钟：domain.ports.Clock 的基础设施实现（测试用假时钟替换）。"""
from __future__ import annotations

import time


class SystemClock:
    """墙钟 + 单调钟，满足 Clock 协议。"""

    def now(self) -> float:
        """epoch 秒：用于衰减结算与持久化时间戳。"""
        return time.time()

    def monotonic(self) -> float:
        """单调秒：不受系统时间回拨影响的计时基准（PRD M5 边界）。"""
        return time.monotonic()
