"""长期事实记忆：只记用户主动说过的名字 / 正在做的事 / 项目名，全程留在本机。

与本地小脑的「会话内记忆」互补：那个重启即忘（隐私边界），这个显式落盘，
由用户在设置里一键关闭或清空。只做轻量正则抽取，不做行为画像、不上传。
"""
from __future__ import annotations

import logging
import re
from typing import Callable

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"(?:我叫|我的名字是|叫我)\s*([\w\u4e00-\u9fa5]{1,12})")
_ACTIVITY_RE = re.compile(r"(?:我在|最近在|正在)(?:做|写|看|学|忙|搞)\s*"
                          r"([\w\u4e00-\u9fa5]{1,20})")
_PROJECT_RE = re.compile(r"(?:我的项目|项目)(?:叫|是|\s*[:：]\s*)\s*"
                         r"([\w\u4e00-\u9fa5\-]{1,20})")
_TAIL_NOISE = "的了呢吧啊呀嘛，。！？,.!? "

_BAD_NAMES = {"什么", "谁", "一个", "不", "没", "很累", "很忙", "有点累"}


def _clean(raw: str) -> str:
    return str(raw or "").strip(_TAIL_NOISE)[:20]


class LongTermMemory:
    def __init__(self, store, enabled_provider: Callable[[], bool],
                 clock=None, ttl_provider: Callable[[], int] | None = None):
        self._store = store
        self._enabled = enabled_provider
        self._clock = clock
        self._ttl_provider = ttl_provider   # 自由事实进提示词的 TTL（天，0=永不）
        self._facts: dict[str, str] = self._store.load()
        self._history: list[dict] = self._store.load_history()
        self._entries: list[dict] = self._store.load_fact_entries()

    def learn_from(self, text: str) -> bool:
        """从一句用户输入里抽取事实；有新增才落盘，返回是否变化。"""
        if not self._enabled():
            return False
        raw = str(text or "").strip()
        if not raw:
            return False
        found: dict[str, str] = {}
        match = _NAME_RE.search(raw)
        if match:
            value = _clean(match.group(1))
            if value and value not in _BAD_NAMES:
                found["user_name"] = value
        match = _ACTIVITY_RE.search(raw)
        if match and _clean(match.group(1)):
            found["activity"] = _clean(match.group(1))
        match = _PROJECT_RE.search(raw)
        if match and _clean(match.group(1)):
            found["project"] = _clean(match.group(1))
        changed = {key: value for key, value in found.items()
                   if value and self._facts.get(key) != value}
        if not changed:
            return False
        self._facts.update(changed)
        from ..domain.chat_record import day_key
        day = day_key()
        self._history.extend({"day": day, "key": key, "value": value}
                             for key, value in changed.items())
        self._history = self._history[-50:]
        self._store.save(self._facts, self._history)
        logger.info("long-term memory updated: %s", sorted(changed))
        return True

    def facts(self) -> str:
        """人话事实摘要；关掉记忆或没记到东西时返回空串。"""
        if not self._enabled():
            return ""
        parts = []
        if self._facts.get("user_name"):
            parts.append(f"名字叫{self._facts['user_name']}")
        if self._facts.get("activity"):
            parts.append(f"最近在{self._facts['activity']}")
        if self._facts.get("project"):
            parts.append(f"项目叫{self._facts['project']}")
        for entry in self._decay_entries():
            parts.append(f"{entry['key']}：{entry['value']}")
        return ("关于用户的长期记忆（来自本机记录）：" + "；".join(parts) + "。"
                if parts else "")

    # ---------- 自由事实（v1.31 实体记忆泛化 / v1.32 衰减可配置） ----------

    #: 进提示词的自由事实条数上限（更多事实靠 agent 的 recall_memories 工具检索）
    PROMPT_ENTRY_LIMIT = 12

    def remember(self, key: str, value: str) -> bool:
        """记住一条自由事实（agent 的 remember 工具 / 未来手动入口）。

        同 key 覆盖旧值并刷新日期；上限 50 条，超出时挤掉最旧的。
        """
        if not self._enabled():
            return False
        key = str(key or "").strip()[:24]
        value = str(value or "").strip()[:120]
        if not key or not value:
            return False
        from ..domain.chat_record import day_key
        day = day_key(self._clock.now() if self._clock else None)
        entry = {"key": key, "value": value, "day": day}
        self._entries = [e for e in self._entries if e.get("key") != key]
        self._entries.append(entry)
        self._entries = self._entries[-50:]
        self._history.append({"day": day, "key": f"记住·{key}", "value": value})
        self._history = self._history[-50:]
        self._store.save_fact_entries(self._entries)
        self._store.save(self._facts, self._history)
        logger.info("fact remembered: %s", key)
        return True

    def entries(self) -> list[dict]:
        return list(self._entries) if self._enabled() else []

    def delete_entry(self, index: int) -> bool:
        """时间线「按条遗忘」一条自由事实。"""
        if not self._enabled() or not self._store.delete_fact_entry(index):
            return False
        self._entries = self._store.load_fact_entries()
        return True

    def _decay_entries(self) -> list[dict]:
        """按配置的 TTL 排序：新的在前；TTL=0 表示永不衰减。

        检索策略（v1.32）：提示词只带最近 N 条防止膨胀，完整记忆永远可以
        通过 agent 的 recall_memories 工具查到——不再替 AI「丢掉真实记忆」。
        """
        import time as _time
        ttl = int(self._ttl_provider()) if self._ttl_provider else 365
        now = _time.time() if self._clock is None else self._clock.now()
        fresh = []
        for entry in reversed(self._entries[-50:]):
            if ttl > 0:
                try:
                    from datetime import datetime
                    age_days = (now - datetime.strptime(
                        str(entry.get("day")), "%Y-%m-%d").timestamp()) / 86_400.0
                except ValueError:
                    age_days = 0.0
                if age_days > ttl:
                    continue
            fresh.append(entry)
            if len(fresh) >= self.PROMPT_ENTRY_LIMIT:
                break
        return fresh

    def prompt_line(self) -> str:
        """拼进系统提示词的增量行；无事实时不改变原提示词（保持字节级兼容）。

        事实由 AI 从对话里自动抽取，覆盖任意话题（名字/喜好/住址/约定…）；
        这里显式声明权威性，避免弱模型把「我没有记忆」当默认回答。
        """
        facts = self.facts()
        if not facts:
            return ""
        return ("\n【长期记忆 · 权威事实】" + facts
                + "用户问「我叫什么 / 我之前说过什么 / 记得我吗 / 我喜欢什么」等"
                  "涉及本人的问题时，直接依据这里回答；这里没有的才说想不起来，"
                  "不要说「我没有记忆」，也不要编。")

    def hydrate(self, engine) -> None:
        """把长期事实回填给本地小脑的会话内记忆，让重启后也认得人。"""
        memory = getattr(engine, "memory", None)
        if memory is None or not self._enabled():
            return
        if self._facts.get("user_name"):
            memory.user_name = self._facts["user_name"]
        if self._facts.get("activity"):
            memory.activity = self._facts["activity"]

    def values(self) -> dict[str, str]:
        return dict(self._facts)

    def timeline(self) -> list[dict]:
        """跨会话事实时间线：什么时候记下了什么，供「我记得你什么」回看。"""
        return list(self._history) if self._enabled() else []

    def reload(self) -> None:
        """时间线按条遗忘后重新读盘，避免内存里仍留着已删的事实。"""
        self._facts = self._store.load()
        self._history = self._store.load_history()
        self._entries = self._store.load_fact_entries()

    def clear(self) -> None:
        self._facts = {"user_name": "", "activity": "", "project": ""}
        self._history = []
        self._entries = []
        self._store.save_fact_entries([])
        self._store.save(self._facts, self._history)
