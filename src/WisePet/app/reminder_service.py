"""ReminderService：喝水/久坐计时、勿扰顺延与合并播报（PRD M6）。"""
from __future__ import annotations

import logging
from typing import Callable

from ..domain.config_model import AppConfig
from ..domain.events import KIND_SEDENTARY, KIND_WATER, ReminderDueEvent

logger = logging.getLogger(__name__)

REMINDER_TEXTS = {
    KIND_WATER: "该喝水啦～补充点水分！",
    KIND_SEDENTARY: "坐了好久啦，站起来活动一下吧！",
}
KINDS = (KIND_WATER, KIND_SEDENTARY)
_SECONDS_PER_MINUTE = 60.0


class ReminderService:
    """到期必响（无全屏勿扰）；可选勿扰时段内顺延到时段结束（v2.0 DnD）。"""

    def __init__(self, clock, bus, config_provider: Callable[[], AppConfig],
                 deferral_provider: Callable[[], float] | None = None):
        self._clock = clock
        self._bus = bus
        self._config = config_provider
        # 返回「距勿扰结束的秒数」；0 表示不在勿扰时段（一句话定时提醒不受影响）
        self._deferral = deferral_provider
        self._due_at: dict[str, float] = {}

    def start(self) -> None:
        """以当前时间重置所有计时（唤醒重算也用它，PRD M6 异常项）。"""
        now = self._clock.now()
        for kind in KINDS:
            self._due_at[kind] = now + self._interval_s(kind)

    def tick(self, now: float | None = None) -> None:
        now = self._clock.now() if now is None else now
        due_kinds = []
        for kind in KINDS:
            if not self._item(kind).enabled:
                continue
            if kind not in self._due_at:
                self._due_at[kind] = now + self._interval_s(kind)
                continue
            if now >= self._due_at[kind]:
                due_kinds.append(kind)
        if not due_kinds:
            return
        if self._deferral is not None:
            defer_s = self._deferral()
            if defer_s > 0:
                for kind in due_kinds:
                    self._due_at[kind] = now + max(30.0, defer_s)
                logger.info("reminders deferred to quiet-hours end: %s", due_kinds)
                return
        # 每个 kind 各发各的事件：多卡堆叠按标题区分，不再合并（合并会让标题张冠李戴）
        for kind in due_kinds:
            self._bus.publish(ReminderDueEvent(kind=kind, text=REMINDER_TEXTS[kind]))
            self._due_at[kind] = now + self._interval_s(kind)

    # ---------- 设置入口（设置窗口/后端热更） ----------

    def set_enabled(self, kind: str, enabled: bool) -> None:
        self._check_kind(kind)
        item = self._item(kind)
        item.enabled = enabled
        if enabled:
            self._due_at[kind] = self._clock.now() + self._interval_s(kind)
        else:
            self._due_at.pop(kind, None)

    def set_interval(self, kind: str, minutes: float) -> None:
        self._check_kind(kind)
        minimum = self._config().reminders.min_interval_min
        if minutes < minimum:
            raise ValueError(f"interval must be >= {minimum} minutes")
        item = self._item(kind)
        item.interval_min = minutes
        if item.enabled:
            # 已关闭的提醒不重新排期，避免设置保存后“关掉的提醒又活了”
            self._due_at[kind] = self._clock.now() + minutes * _SECONDS_PER_MINUTE

    def notify_ack(self, kind: str) -> None:
        """用户点击"知道啦"：本轮已按间隔排期，这里仅关闭气泡（ui 处理）。"""
        logger.debug("reminder acked: %s", kind)

    # ---------- 内部 ----------

    @staticmethod
    def _check_kind(kind: str) -> None:
        if kind not in KINDS:
            raise ValueError(f"unknown reminder kind: {kind}")

    def _item(self, kind: str):
        reminders = self._config().reminders
        return reminders.water if kind == KIND_WATER else reminders.sedentary

    def _interval_s(self, kind: str) -> float:
        return max(1.0, self._item(kind).interval_min) * _SECONDS_PER_MINUTE
