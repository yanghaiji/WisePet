"""GrowthService：把互动（喂食/抚摸/聊天/每日陪伴）折算成成长积分并持久化。

对外只暴露三个动作：互动发生时累加（on_feed/on_pet/on_chat）、每天第一次活动补
陪伴分（tick）、当前成长快照（current，供统计面板）。升级/换形态时发一条
SpeakRequestedEvent，复用既有气泡通道播报「长大了一点」。
所有写入失败都不抛出（成长是锦上添花，绝不能拖累主流程）。
"""
from __future__ import annotations

import logging
from typing import Callable

from ..domain.events import SpeakRequestedEvent
from ..domain.growth import (
    CHAT_POINTS,
    COMPANION_DAY_POINTS,
    FEED_POINTS,
    PET_POINTS,
    GrowthVO,
    level_from_points,
    stage_for_level,
)

logger = logging.getLogger(__name__)


class GrowthService:
    def __init__(self, clock, bus, store,
                 config_provider: Callable[[], object] | None = None,
                 day_key: Callable[[float], str] | None = None):
        self._clock = clock
        self._bus = bus
        self._store = store
        self._config = config_provider
        self._day_key = day_key or _default_day_key
        self._data = store.load()
        # 开机先校准陪伴天数（跨过若干天没启动也能补齐观感），不发播报
        self._touch_day(publish=False)

    # ---------- 查询 ----------

    def current(self) -> GrowthVO:
        return GrowthVO.from_points(self._data.points, self._data.companion_days)

    # ---------- 互动累加 ----------

    def on_feed(self) -> None:
        self._add(FEED_POINTS, count_attr="fed")

    def on_pet(self) -> None:
        self._add(PET_POINTS, count_attr="petted")

    def on_chat(self) -> None:
        self._add(CHAT_POINTS, count_attr="chatted")

    # ---------- 周期 ----------

    def tick(self, now: float | None = None) -> None:
        """跨到新的自然日且开着成长：补一次「陪伴满一天」的积分。"""
        now = self._clock.now() if now is None else now
        self._touch_day(now=now, publish=True)

    # ---------- 内部 ----------

    def _enabled(self) -> bool:
        if self._config is None:
            return True
        growth = getattr(self._config(), "growth", None)
        return True if growth is None else bool(growth.enabled)

    def _add(self, points: int, *, count_attr: str | None = None) -> None:
        if not self._enabled():
            return
        self._bump(points)
        if count_attr is not None:
            setattr(self._data, count_attr, getattr(self._data, count_attr) + 1)
        self._persist()

    def _touch_day(self, now: float | None = None, *, publish: bool) -> None:
        if not self._enabled():
            return
        today = self._day_key(self._clock.now() if now is None else now)
        if self._data.last_day == today:
            return
        self._data.last_day = today
        self._data.companion_days += 1
        self._bump(COMPANION_DAY_POINTS)
        self._persist()

    def _bump(self, points: int) -> None:
        """累加积分并检测升级/换形态；只在成长真正变化时播报一次。"""
        before_level = level_from_points(self._data.points)
        self._data.points = max(0, self._data.points + points)
        after_level = level_from_points(self._data.points)
        if after_level > before_level and self._enabled():
            self._announce_growth(after_level)

    def _announce_growth(self, level: int) -> None:
        stage = stage_for_level(level)
        prev_stage = stage_for_level(level - 1)
        text = (f"我升到 {level} 级啦～现在是{stage}，"
                f"谢谢你一直陪着我！" if stage == prev_stage
                else f"哇，我进入{stage}了！（{level} 级）谢谢你陪我长大～")
        try:
            self._bus.publish(SpeakRequestedEvent(text, source="system", title="成长"))
        except Exception:  # noqa: BLE001 - 播报失败不影响成长数据
            logger.debug("growth announce failed", exc_info=True)

    def _persist(self) -> None:
        try:
            self._store.save(self._data)
        except Exception:  # noqa: BLE001 - 落盘尽力而为
            logger.debug("growth persist failed", exc_info=True)


def _default_day_key(ts: float) -> str:
    import time
    return time.strftime("%Y-%m-%d", time.localtime(ts))
