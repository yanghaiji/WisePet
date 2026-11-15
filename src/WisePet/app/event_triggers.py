"""EventTriggerService：从「按点找你」变成「看情况找你」。

只吃本机可观测事实：系统空闲时长（platform_idle，非 Windows 降级为 None）、
系统时钟、今日完成专注次数。规则是纯函数，便于单测；触发按「规则 × 自然日」
去重、按每日总量封顶，所有推送照旧过打扰预算。

刻意不接天气/日历/联网 API：与零依赖、断网可用的底线冲突。
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from ..domain.chat_record import day_key
from ..domain.events import SpeakRequestedEvent

logger = logging.getLogger(__name__)

RULE_RETURN = "return_from_idle"
RULE_LATE_NIGHT = "late_night"
RULE_FOCUS_STREAK = "focus_streak"

#: 人走开多久（秒）再回来，才值得打招呼
AWAY_S = 1800.0
#: 回来/深夜判定中的「正在用电脑」阈值
ACTIVE_S = 120.0

EVENT_LINES = {
    RULE_RETURN: "回来啦～我不出声地等你呢，先喝口水吧。",
    RULE_LATE_NIGHT: "这么晚还在忙呀，记得早点休息，我陪你收个尾。",
    RULE_FOCUS_STREAK: "今天已经连着专注好几轮啦，真的很拼，歇会儿再继续～",
}
EVENT_TITLES = {
    RULE_RETURN: "你回来啦",
    RULE_LATE_NIGHT: "夜深了",
    RULE_FOCUS_STREAK: "今天很拼",
}


def evaluate(*, hour: int, idle_s: float | None, prev_idle: float | None,
             today_focus_count: int, fired_today: set[str],
             daily_limit: int) -> list[str]:
    """纯函数：给定此刻事实，返回该触发的规则 id 列表（已去重/封顶）。"""
    if daily_limit <= 0 or len(fired_today) >= daily_limit:
        return []
    hits: list[str] = []
    if RULE_RETURN not in fired_today and idle_s is not None and prev_idle is not None:
        if prev_idle >= AWAY_S and idle_s < ACTIVE_S:
            hits.append(RULE_RETURN)
    if (RULE_LATE_NIGHT not in fired_today and hour >= 23
            and idle_s is not None and idle_s < ACTIVE_S):
        hits.append(RULE_LATE_NIGHT)
    if RULE_FOCUS_STREAK not in fired_today and today_focus_count >= 2:
        hits.append(RULE_FOCUS_STREAK)
    return hits[:max(0, daily_limit - len(fired_today))]


class EventTriggerService:
    def __init__(self, clock, bus, config_provider: Callable[[], object], store,
                 idle_provider: Callable[[], float | None],
                 focus_count_provider: Callable[[], int]):
        self._clock = clock
        self._bus = bus
        self._config = config_provider
        self._store = store
        self._idle = idle_provider
        self._focus_count = focus_count_provider
        state = store.load()
        self._day = str(state.get("day") or "")
        self._fired: set[str] = set(state.get("fired") or [])
        self._prev_idle: float | None = None
        self._last_check_at = 0.0

    # ---------- 周期驱动 ----------

    def tick(self, now: float | None = None) -> None:
        now = self._clock.now() if now is None else now
        cfg = self._config().assistant
        if not cfg.enabled or not cfg.events_enabled:
            return
        today = day_key(now)
        if today != self._day:
            self._day, self._fired = today, set()
            self._persist()
        # 空闲判定 30s 一次就够，避免每秒调系统 API
        if now - self._last_check_at < 30.0:
            return
        self._last_check_at = now
        try:
            idle_s = self._idle()
            focus_count = int(self._focus_count() or 0)
        except Exception:  # noqa: BLE001 - 平台 API 尽力而为
            logger.debug("event trigger source failed", exc_info=True)
            return
        if idle_s is not None:
            idle_s = max(0.0, float(idle_s))
        hits = evaluate(
            hour=time.localtime(now).tm_hour,
            idle_s=idle_s, prev_idle=self._prev_idle,
            today_focus_count=focus_count, fired_today=self._fired,
            daily_limit=int(cfg.event_daily_limit))
        self._prev_idle = idle_s
        for rule in hits:
            self._fired.add(rule)
            self._bus.publish(SpeakRequestedEvent(
                EVENT_LINES[rule], source="cheer", title=EVENT_TITLES[rule]))
            logger.info("event trigger fired: %s", rule)
        if hits:
            self._persist()

    def _persist(self) -> None:
        self._store.save({"day": self._day, "fired": sorted(self._fired)})
