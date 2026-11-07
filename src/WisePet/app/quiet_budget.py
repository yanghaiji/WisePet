"""打扰预算：主动推送的每日总量 / 单来源上限 / 一键免打扰的统一闸门。

只拦「程序主动推送」，用户主动发起的聊天回复与工具执行反馈不走这里，
否则会出现「问了却没回」被误判为 bug（AGENT.md §3 的多轮真机教训）。
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from ..domain.config_model import AppConfig
from ..domain.product import is_push_source

logger = logging.getLogger(__name__)


class QuietBudget:
    """在唯一发布口判定这条推送能不能发；额度按自然日滚动，落盘可查。"""

    def __init__(self, store, clock, config_provider: Callable[[], AppConfig]):
        self._store = store
        self._clock = clock
        self._config = config_provider
        self._data = self._store.load()
        self._exhausted = False
        self._notified_day: str | None = None

    def _roll_day(self) -> dict:
        day = time.strftime("%Y-%m-%d", time.localtime(self._clock.now()))
        if self._data.get("day") != day:
            self._data = {"day": day, "total": 0, "by_source": {}}
            self._exhausted = False
            self._store.save(self._data)
        by_source = self._data.get("by_source")
        if not isinstance(by_source, dict):
            self._data["by_source"] = {}
        return self._data

    def allow(self, source: str, push: bool | None = None) -> bool:
        """返回是否放行；放行时同时记一笔配额（调用方随后才真正发出）。

        push 由组装根显式给出（它知道事件是不是「程序主动推送」）；
        不传时按来源白名单兜底，单测与离线探针可直接用默认行为。
        """
        cfg = self._config().quiet
        is_push = is_push_source(source) if push is None else push
        if not cfg.enabled or not is_push:
            return True                 # 关掉预算 / 用户主动发起：一律放行
        if cfg.muted:
            return False                # 一键免打扰：主动推送全停
        data = self._roll_day()
        used = int(data.get("total") or 0)
        by_source = data["by_source"]
        source_used = int(by_source.get(source) or 0)
        if used >= cfg.daily_limit or source_used >= cfg.per_source_limit:
            self._exhausted = True
            logger.info("quiet budget blocked push: source=%s day=%s total=%d/%d source=%d/%d",
                        source, data.get("day"), used, cfg.daily_limit,
                        source_used, cfg.per_source_limit)
            return False
        data["total"] = used + 1
        by_source[source] = source_used + 1
        self._store.save(data)
        return True

    def exhaustion_hint(self) -> str | None:
        """额度刚用尽时给一句解释，每天只给一次。

        静默拦截是产品上最糟糕的失败形态：用户只会看到「弹窗都没了」却
        不知道为什么。这条提示由组装根以 `source="system"` 直接走气泡，
        不经过预算闸门，也不会变成新的循环打扰。
        """
        if not self._exhausted:
            return None
        day = time.strftime("%Y-%m-%d", time.localtime(self._clock.now()))
        if self._notified_day == day:
            return None
        self._notified_day = day
        cfg = self._config().quiet
        return (f"今天主动提醒额度用完了（每天 {cfg.daily_limit} 条、"
                f"同类 {cfg.per_source_limit} 条），后面我就安静啦。"
                "想多弹几次，到设置 →「体验与隐私」调打扰预算，"
                "或右键 →「一键场景包」→「健康作息」恢复。")

    def usage(self) -> dict:
        """给设置/统计看的当日用量（不触发写入）。"""
        day = time.strftime("%Y-%m-%d", time.localtime(self._clock.now()))
        if self._data.get("day") != day:
            return {"day": day, "total": 0, "by_source": {}}
        return {"day": day, "total": int(self._data.get("total") or 0),
                "by_source": dict(self._data.get("by_source") or {})}
