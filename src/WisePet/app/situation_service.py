"""SituationService：把用户明说的「近期处境」变成自然的体贴。

观察对话 → 识别近况（客观事件 + 用户说出口的情绪吐槽）→ 持久化（带时效）→
① 注入系统提示词让回复贴合；② 处境新鲜时偶尔一句关心（走 QuietBudget）。

只认用户第一人称明确说出口的话，不推测脑补、绝不联网、绝不上传；
识别不到就不动，宁可漏不可乱。
"""
from __future__ import annotations

import logging
from typing import Callable

from ..domain.chat_record import day_key
from ..domain.events import SpeakRequestedEvent
from ..domain.situation import (
    Situation, detect_situation, make_situation, should_care,
    situation_care_line, situation_prompt_line,
)

logger = logging.getLogger(__name__)


class SituationService:
    def __init__(self, clock, bus, store, config_provider: Callable[[], object],
                 name_provider: Callable[[], str] | None = None):
        self._clock = clock
        self._bus = bus
        self._store = store
        self._config = config_provider
        self._name = name_provider or (lambda: "小墨")
        self._last_check = 0.0     # tick 节流：无需每秒读盘

    @staticmethod
    def _enabled(cfg) -> bool:
        comp = getattr(cfg, "companion", None)
        return bool(getattr(comp, "enabled", True)
                    and getattr(comp, "situation_enabled", True))

    def _records(self) -> list[Situation]:
        out = []
        for item in self._store.load():
            try:
                since = float(item.get("since") or 0)
                day = str(item.get("last_cared_day") or "")
                at = float(item.get("last_cared_at") or 0)
                count = int(item.get("care_count") or 0)
                if day and not count:      # 旧格式（只有自然日）迁移：当作已关怀一次
                    count, at = 1, since
                out.append(Situation(
                    kind=str(item.get("kind") or ""),
                    since=since,
                    expires_at=float(item.get("expires_at") or 0),
                    last_cared_day=day, last_cared_at=at, care_count=count))
            except (TypeError, ValueError):
                continue
        return out

    def _save(self, records: list[Situation]) -> None:
        self._store.save([{
            "kind": r.kind, "since": r.since, "expires_at": r.expires_at,
            "last_cared_day": r.last_cared_day, "last_cared_at": r.last_cared_at,
            "care_count": r.care_count} for r in records])

    def _upsert(self, situation: Situation) -> None:
        """同类刷新时效（先删旧同类再追加新的，不叠加）。

        保留旧的关怀进度（日期/时间戳/次数）：用户再聊一次触发重新识别时，
        不能擦掉「已关怀」状态，否则会反复弹（真机日志实证一天弹 3 次），
        也会把已经开始的跟进序列打回首次。
        """
        records = self._records()
        old = next((r for r in records if r.kind == situation.kind), None)
        if old is not None and (old.care_count or old.last_cared_at or old.last_cared_day):
            situation = Situation(situation.kind, situation.since, situation.expires_at,
                                  old.last_cared_day, old.last_cared_at, old.care_count)
        records = [r for r in records if r.kind != situation.kind]
        records.append(situation)
        self._save(records)
        logger.info("situation learned: %s until %s (cared=%s)", situation.kind,
                    situation.expires_at, situation.care_count)

    def observe(self, role: str, content: str) -> None:
        """关键词兜底（无 AI Key）：处理用户说明的客观近况/情绪吐槽，同类刷新时效。"""
        if str(role) != "user" or not self._enabled(self._config()):
            return
        now = self._clock.now()
        detected = detect_situation(content, now)
        if detected is not None:
            self._upsert(detected)

    def learn_from_ai(self, kinds: list[str]) -> None:
        """主路径（有 AI Key）：AutoFactExtractor 同一次调用识别的处境类别。

        AI 能听懂任意说法（「这周忙疯了」→ overtime、「累得不想动」→ tired），
        类别经白名单校验，时效由代码按类别决定——模型碰不到脑补判断与时长。
        """
        if not self._enabled(self._config()):
            return
        now = self._clock.now()
        for kind in (kinds or []):
            situation = make_situation(str(kind), now)
            if situation is not None:
                self._upsert(situation)

    def active(self, now: float | None = None) -> Situation | None:
        """当前有效的处境（最近识别的优先）；无则返回 None。"""
        now = self._clock.now() if now is None else now
        actives = [r for r in self._records() if r.active(now)]
        return max(actives, key=lambda r: r.since) if actives else None

    def prompt_line(self, now: float | None = None) -> str:
        now = self._clock.now() if now is None else now
        if not self._enabled(self._config()):
            return ""
        return situation_prompt_line(self.active(now), now)

    def tick(self, now: float | None = None) -> None:
        """到关怀时机就说一句（首次/跟进）；顺手清理过期记录。"""
        if not self._enabled(self._config()):
            return
        now = self._clock.now() if now is None else now
        if now - self._last_check < 30.0:      # 30s 内不重复读盘
            return
        self._last_check = now
        records = self._records()
        alive = [r for r in records if r.active(now)]
        if len(alive) != len(records):
            self._save(alive)                      # 过期自然消退
        if not alive:
            return
        situation = max(alive, key=lambda r: r.since)
        if not should_care(situation, now):
            return
        line = situation_care_line(situation, now)
        if not line:
            return
        # 先标记再发布，避免并发/重复 tick 在同一节奏上弹两次
        for i, r in enumerate(alive):
            if r.kind == situation.kind:
                alive[i] = Situation(r.kind, r.since, r.expires_at,
                                     day_key(now), now, r.care_count + 1)
        self._save(alive)
        self._bus.publish(SpeakRequestedEvent(
            line, source="system", title="我在呢"))   # 标题在 PUSH_TITLES，走 QuietBudget
        logger.info("situation care fired: %s (care #%d)", situation.kind,
                    situation.care_count + 1)
