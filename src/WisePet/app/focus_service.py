"""FocusService：番茄钟——「陪我专注25分钟」，到点提醒休息并记入专注统计。

会话只存内存（重启即弃，专注本来就是当下的事）；历史记录落盘 focus.json 供统计。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable

from ..domain.events import SpeakRequestedEvent

logger = logging.getLogger(__name__)

_SECONDS_PER_MINUTE = 60.0
DEFAULT_FOCUS_MIN = 25.0
MAX_FOCUS_MIN = 180.0


@dataclass(frozen=True)
class FocusSession:
    date: str          # YYYY-MM-DD
    started_at: float
    minutes: float


class FocusLog:
    """专注历史（~/.WisePet/focus.json），只增不改；统计面板读取。"""

    def __init__(self, path: Callable[[], object] | None = None, store=None):
        self._store = store
        self._path = path

    # 供 ui 统计读取：today_minutes / today_sessions 由组装根基于记录计算
    @staticmethod
    def summarize(entries: list[dict], today: str) -> tuple[float, int]:
        minutes = sum(float(e.get("minutes") or 0) for e in entries
                      if e.get("date") == today)
        count = sum(1 for e in entries if e.get("date") == today)
        return minutes, count


class FocusService:
    def __init__(self, clock, bus, append_entry: Callable[[dict], None]):
        self._clock = clock
        self._bus = bus
        self._append_entry = append_entry
        self._due_at: float | None = None
        self._started_at: float | None = None
        self._minutes: float = 0.0

    # ---------- 状态 ----------

    @property
    def running(self) -> bool:
        return self._due_at is not None

    @property
    def minutes_left(self) -> float:
        if self._due_at is None:
            return 0.0
        return max(0.0, (self._due_at - self._clock.now()) / _SECONDS_PER_MINUTE)

    # ---------- 命令 ----------

    def start(self, minutes: float = DEFAULT_FOCUS_MIN) -> float:
        """开始一轮专注；重复调用视为重新计时。返回结束时刻。"""
        minutes = min(MAX_FOCUS_MIN, max(1.0, float(minutes)))
        self._minutes = minutes
        self._started_at = self._clock.now()
        self._due_at = self._started_at + minutes * _SECONDS_PER_MINUTE
        logger.info("focus session started: %.0f min", minutes)
        return self._due_at

    def cancel(self) -> bool:
        if self._due_at is None:
            return False
        self._due_at = None     # 取消的不计入专注统计，只有完成的才落盘
        logger.info("focus session cancelled")
        return True

    def tick(self, now: float | None = None) -> None:
        """到点：休息提醒 + 落盘统计。"""
        now = self._clock.now() if now is None else now
        if self._due_at is None or now < self._due_at:
            return
        self._due_at = None
        self._log_session()
        self._bus.publish(SpeakRequestedEvent(
            f"专注 {self._minutes:.0f} 分钟完成，起来伸个懒腰、喝口水吧！",
            source="focus", title="专注结束"))

    # ---------- 内部 ----------

    def _log_session(self) -> None:
        entry = {
            "date": time.strftime("%Y-%m-%d", time.localtime(self._started_at or time.time())),
            "started_at": self._started_at or time.time(),
            "minutes": self._minutes,
        }
        try:
            self._append_entry(entry)
        except OSError:
            logger.exception("focus log append failed")
