"""CompanionService：把「一直记得你」变成可运行的行为。

三件事：
- 启动时算出离开多久（presence.json 精确时间戳），给开机问候一句「好久不见」；
- 从本地聊天历史里挑一条还没兑现的惦记，注入系统提示词 + 低频主动提起一条；
- 去重按自然日，跨重启有效；所有推送走唯一 SpeakRequestedEvent 出口，照旧过打扰预算。

纯本地事实，不联网、不画像、不猜测情绪；AI 只负责把话接得自然。
"""
from __future__ import annotations

import logging
from typing import Callable

from ..domain.chat_record import day_key
from ..domain.companion import (
    RecallCandidate, is_done_signal, pick_recalls, recall_line,
    recall_prompt_lines, resolve_key, reunion_greeting, reunion_hint,
)
from ..domain.events import SpeakRequestedEvent

logger = logging.getLogger(__name__)

#: 启动后多久才主动提起惦记（避开开机问候/引导挤在一起）
RECALL_DELAY_S = 180.0
_SECONDS_PER_HOUR = 3600.0


class CompanionService:
    def __init__(self, clock, bus, config_provider: Callable[[], object],
                 records_provider: Callable[[], list], store,
                 name_provider: Callable[[], str]):
        self._clock = clock
        self._bus = bus
        self._config = config_provider
        self._records = records_provider
        self._store = store
        self._name = name_provider
        self._start_at = clock.now()
        self._gap_hours = 0.0
        self._candidate: RecallCandidate | None = None
        self._candidates: list[RecallCandidate] = []
        self._candidate_loaded = False
        self._recall_fired = False

    # ---------- 启动 ----------

    def boot(self) -> float:
        """必须先于其它服务刷新今天：读旧 last_seen → 算间隔 → 写入本次时间。

        返回离开的小时数；首次安装（旧值 0）返回 0，不装作久别。
        """
        now = self._clock.now()
        previous = self._store.load().get("last_seen_ts") or 0.0
        self._gap_hours = max(0.0, (now - previous) / _SECONDS_PER_HOUR) \
            if previous > 0 else 0.0
        self._store.touch_seen(now)
        if self._gap_hours > 0:
            logger.info("presence gap: %.1f hours", self._gap_hours)
        return self._gap_hours

    def reunion_text(self) -> str | None:
        """久别重逢的本地话术；不够久或用户关了开关则返回 None。"""
        cfg = self._config().companion
        if not cfg.enabled or not cfg.reunion_enabled:
            return None
        return reunion_greeting(self._gap_hours, self._name(),
                                min_hours=float(cfg.reunion_min_hours))

    def reunion_prompt_hint(self) -> str:
        cfg = self._config().companion
        if not cfg.enabled or not cfg.reunion_enabled:
            return ""
        return reunion_hint(self._gap_hours,
                            min_hours=float(cfg.reunion_min_hours))

    # ---------- 主动引用 ----------

    def _load_candidates(self) -> list[RecallCandidate]:
        """懒加载一组惦记（排除已提过 / 已消解的指纹），只在运行时算一次。"""
        if self._candidate_loaded:
            # 兼容外部（含测试）直接注入 _candidate 的场景
            if self._candidates:
                return self._candidates
            return [self._candidate] if self._candidate else []
        cfg = self._config().companion
        self._candidate_loaded = True
        if not cfg.enabled or not cfg.memory_prompt_enabled:
            return []
        try:
            exclude = (self._store.excluded_keys()
                       if hasattr(self._store, "excluded_keys") else frozenset())
            found = pick_recalls(
                self._records(), now=self._clock.now(),
                lookback_days=int(cfg.recall_lookback_days),
                limit=int(getattr(cfg, "max_recalls", 3)), exclude=exclude)
        except (OSError, ValueError, TypeError, AttributeError):
            logger.exception("recall candidates scan failed")
            found = []
        self._candidates = found
        self._candidate = found[0] if found else None
        return found

    def _load_candidate(self) -> RecallCandidate | None:
        found = self._load_candidates()
        return found[0] if found else None

    def recall_candidate(self) -> RecallCandidate | None:
        """最近一条惦记（兼容旧接口）。"""
        return self._load_candidate()

    def recall_candidates(self) -> list[RecallCandidate]:
        """当前惦记清单（供命令面板展示，不触发推送）。"""
        return list(self._load_candidates())

    def prompt_line(self) -> str:
        """系统提示词增量：多条惦记一起给，但明确要求别一口气全倒出来。"""
        return recall_prompt_lines(self._load_candidates())

    def resolve(self, text: str) -> int:
        """用户说「搞定了」→ 把最近提过（或最新）的惦记标记为已兑现。

        之后不再提起，也不会出现在提示词里；返回消解的条数。
        """
        if not is_done_signal(text):
            return 0
        keys = list((self._store.load().get("recalled_keys") or [])
                    if hasattr(self._store, "load") else [])
        key = keys[-1] if keys else ""
        if not key:
            candidates = self._load_candidates()
            if not candidates:
                return 0
            key = resolve_key(candidates[0])
        if not hasattr(self._store, "resolve_recall"):
            return 0
        self._store.resolve_recall(key)
        self.reset_candidates()
        logger.info("recall resolved: %s", key)
        return 1

    def reset_candidates(self) -> None:
        """时间线清了惦记标记 / 用户消解后，强制下次重新扫描历史。"""
        self._candidate_loaded = False
        self._candidates = []
        self._candidate = None
        self._recall_fired = False

    def tick(self, now: float | None = None) -> None:
        """启动一阵后、当天没提过时，从清单里挑一条没提过的轻轻问一句。"""
        cfg = self._config().companion
        if not cfg.enabled or not cfg.recall_enabled or self._recall_fired:
            return
        now = self._clock.now() if now is None else now
        if now - self._start_at < RECALL_DELAY_S:
            return
        state = self._store.load()
        today = day_key(now)
        if state.get("last_recall_day") == today:
            self._recall_fired = True
            return
        candidates = self._load_candidates()
        if not candidates:
            # 本次启动没有惦记：不再每秒读一次去重文件，下次启动自然重算
            self._recall_fired = True
            return
        candidate = candidates[0]
        self._recall_fired = True
        self._store.mark_recall(today)
        if hasattr(self._store, "mark_recall_key"):
            self._store.mark_recall_key(resolve_key(candidate))
        text = recall_line(candidate, self._name())
        self._bus.publish(SpeakRequestedEvent(text, source="cheer", title="我还记得"))
        logger.info("companion recall published: days_ago=%d", candidate.days_ago)
