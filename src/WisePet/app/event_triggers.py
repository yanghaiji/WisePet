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

from ..domain.care import care_term, context_score
from ..domain.chat_record import day_key
from ..domain.events import SpeakRequestedEvent

logger = logging.getLogger(__name__)

RULE_RETURN = "return_from_idle"
RULE_LATE_NIGHT = "late_night"
RULE_FOCUS_STREAK = "focus_streak"
RULE_SOLAR_TERM = "solar_term_care"          # 节气关怀（本地近似，非联网 API）
RULE_WEATHER = "weather_care"                # 天气关怀（可选联网，默认关闭）

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


def solar_term_line(term: str) -> str:
    """把节气名翻译成一句有人情味的关怀话术（纯函数）。"""
    return f"今天是{term}，季节换了个脚步，记得照顾好自己呀～"


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
                 focus_count_provider: Callable[[], int],
                 *,
                 term_provider: Callable[[float], str | None] | None = None,
                 dismiss_rate_provider: Callable[[], float | None] | None = None,
                 weather_line_provider: Callable[..., str] | None = None):
        self._clock = clock
        self._bus = bus
        self._config = config_provider
        self._store = store
        self._idle = idle_provider
        self._focus_count = focus_count_provider
        # 节气关怀：默认按 tick 传入的时间取近似节气；可注入便于测试。
        self._term = term_provider or care_term
        self._dismiss_rate = dismiss_rate_provider
        # 天气关怀：组装根注入 WeatherService.due_line（读缓存、零网络）
        self._weather_line = weather_line_provider
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
        # 节气关怀：也是一条候选，和情境规则共用同一天的去重与总量上限，
        # 并先过情境评分（专注/勿扰/深夜/高忽略率一律不打招呼）。
        term = self._term(now)
        remaining = int(cfg.event_daily_limit) - len(self._fired)
        if (term and RULE_SOLAR_TERM not in self._fired and remaining > 0
                and context_score(
                    hour=time.localtime(now).tm_hour,
                    dismiss_rate=(self._dismiss_rate() if self._dismiss_rate
                                  else None)) > 0.0):
            self._fired.add(RULE_SOLAR_TERM)
            self._bus.publish(SpeakRequestedEvent(
                solar_term_line(term), source="cheer", title=f"{term}到啦"))
            logger.info("event trigger fired: solar_term=%s", term)
        # 天气播报：用户配置的「每隔 N 分钟」提醒（像喝水/久坐），
        # 独立于情境推送额度——不占 event_daily_limit，避免被「回来/深夜」挤掉。
        # 周期去重由 WeatherService 内部做（按间隔），这里「有话才说」，
        # 不塞进当日 fired 集合（否则一天只会报一次，与间隔语义冲突）。
        # 仍过情境分（勿扰/深夜不打招呼）与统一 QuietBudget。
        if self._weather_line is not None:
            try:
                w_line = self._weather_line(now, day_key_fn=day_key)
            except Exception:  # noqa: BLE001 - 天气增强失败静默跳过
                logger.debug("weather line failed", exc_info=True)
                w_line = ""
            if w_line and context_score(
                    hour=time.localtime(now).tm_hour,
                    dismiss_rate=(self._dismiss_rate() if self._dismiss_rate
                                  else None)) > 0.0:
                self._bus.publish(SpeakRequestedEvent(
                    w_line, source="weather", title="今日天气"))
                logger.info("event trigger fired: weather care")
        if hits or RULE_SOLAR_TERM in self._fired:
            self._persist()

    def _persist(self) -> None:
        self._store.save({"day": self._day, "fired": sorted(self._fired)})
