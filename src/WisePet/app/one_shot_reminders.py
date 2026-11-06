"""OneShotReminders：一句话安排的提醒（「5分钟后」「每天14:30」「取消提醒」）。

与喝水/久坐的周期提醒（ReminderService）互不干扰。可选注入持久化回调
（组装根用 infra 仓储实现）：重启后未到期提醒自动恢复、每日提醒续排下一天。
可选注入 polisher（AI 润色话术）：失败回落原文，提醒本身绝不因润色失败而不响。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from ..domain.events import SpeakRequestedEvent
from .action_protocol import MAX_DELAY_MIN, RemindAction

logger = logging.getLogger(__name__)

#: 最多同时挂起的提醒：防刷屏也防内存无界增长
MAX_PENDING = 20


class OneShotReminders:
    def __init__(self, clock, bus, polisher: Callable[[str], str | None] | None = None,
                 load_entries: Callable[[], list[dict]] | None = None,
                 save_entries: Callable[[list[dict]], None] | None = None):
        self._clock = clock
        self._bus = bus
        self._polisher = polisher
        self._load_entries = load_entries
        self._save_entries = save_entries
        # (due_at, text, daily)；daily 形如 "14:30"，触发后续排明天
        self._items: list[dict] = []
        self._load()

    # ---------- 命令 ----------

    def schedule(self, action: RemindAction) -> float:
        if action.delay_min > 0:
            return self.schedule_delay(action.text, action.delay_min)
        hh, mm = action.at_hm.split(":")
        return self.schedule_at(action.text, int(hh), int(mm), daily=action.daily)

    def schedule_delay(self, text: str, delay_min: float) -> float:
        text = self._validate(text, delay_min)
        due_at = self._clock.now() + max(0.05, delay_min) * 60.0
        return self._push(due_at, text, daily="")

    def schedule_at(self, text: str, hour: int, minute: int,
                    daily: bool = False) -> float:
        """今天的 hh:mm；已过则排到明天（合理解释「下午3点提醒我」）。daily=True 每天重复。"""
        text = self._validate(text)
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError(f"bad time: {hour:02d}:{minute:02d}")
        due_at = self._next_occurrence(hour, minute)
        return self._push(due_at, text, daily=f"{hour:02d}:{minute:02d}" if daily else "")

    def cancel_all(self) -> int:
        """清空全部挂起提醒（含每日提醒）；返回清除的数量。"""
        count = len(self._items)
        self._items.clear()
        self._save()
        if count:
            logger.info("reminders cancelled: %d", count)
        return count

    def tick(self, now: float | None = None) -> None:
        """到点的提醒经 SpeakRequestedEvent 播报；每日提醒续排下一天。"""
        now = self._clock.now() if now is None else now
        fired = False
        for item in [it for it in self._items if it["due_at"] <= now]:
            self._items.remove(item)
            fired = True
            logger.info("reminder due: %s (daily=%s)", item["text"], item["daily"])
            self._announce(item["text"])
            if item["daily"]:
                hour, minute = (int(part) for part in item["daily"].split(":"))
                item["due_at"] = self._next_occurrence(hour, minute)
                self._items.append(item)
        if fired:
            self._items.sort(key=lambda it: it["due_at"])
            self._save()

    @property
    def pending(self) -> list[tuple[float, str]]:
        """挂起列表 [(due_at, text)]，按到点时间升序（测试与统计用）。"""
        return [(it["due_at"], it["text"]) for it in sorted(
            self._items, key=lambda it: it["due_at"])]

    # ---------- 播报（可选 AI 润色） ----------

    def _announce(self, text: str) -> None:
        if self._polisher is None:
            self._publish(text)
            return
        threading.Thread(target=self._announce_async, args=(text,),
                         name="wisepet-remind-polish", daemon=True).start()

    def _announce_async(self, text: str) -> None:
        polished = None
        try:
            polished = self._polisher(text)
        except Exception:  # noqa: BLE001 - 润色失败也要响，回落原文
            logger.exception("reminder polish failed")
        self._publish(polished or text)

    def _publish(self, text: str) -> None:
        self._bus.publish(SpeakRequestedEvent(text, source="reminder", title="定时提醒"))

    # ---------- 内部 ----------

    def _next_occurrence(self, hour: int, minute: int) -> float:
        now = self._clock.now()
        due_at = time.mktime(time.localtime(now)[:3] + (hour, minute, 0, 0, 0, 0))
        if due_at <= now:
            due_at += 86400.0
        return due_at

    def _push(self, due_at: float, text: str, daily: str) -> float:
        self._items.append({"due_at": due_at, "text": text, "daily": daily})
        self._items.sort(key=lambda it: it["due_at"])
        del self._items[MAX_PENDING:]       # 保留最早到点的，丢最远的
        self._save()
        return due_at

    @staticmethod
    def _validate(text: str, delay_min: float | None = None) -> str:
        stripped = str(text or "").strip()
        if not stripped:
            raise ValueError("reminder text required")
        if delay_min is not None and not 0 < delay_min <= MAX_DELAY_MIN:
            raise ValueError(f"delay out of range: {delay_min}")
        return stripped[:100]

    # ---------- 持久化 ----------

    def _save(self) -> None:
        if self._save_entries is None:
            return
        try:
            self._save_entries(self._items)
        except OSError:
            logger.exception("reminders save failed")

    def _load(self) -> None:
        if self._load_entries is None:
            return
        try:
            now = self._clock.now()
            for item in self._load_entries():
                text = str(item.get("text") or "").strip()
                daily = str(item.get("daily") or "")
                if not text:
                    continue
                if daily:                       # 每日提醒：续排下一次
                    hour, minute = (int(p) for p in daily.split(":"))
                    self._items.append({"due_at": self._next_occurrence(hour, minute),
                                        "text": text, "daily": daily})
                    continue
                due_at = float(item.get("due_at") or 0)
                if due_at > now:                # 一次性：没到期的恢复，过期的丢弃
                    self._items.append({"due_at": due_at, "text": text, "daily": ""})
            self._items.sort(key=lambda it: it["due_at"])
            if self._items:
                logger.info("reminders restored: %d", len(self._items))
        except (OSError, ValueError, TypeError, AttributeError):
            logger.exception("reminders load failed, starting empty")
            self._items = []
