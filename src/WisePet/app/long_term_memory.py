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
    def __init__(self, store, enabled_provider: Callable[[], bool]):
        self._store = store
        self._enabled = enabled_provider
        self._facts: dict[str, str] = self._store.load()

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
        self._store.save(self._facts)
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
        return ("关于用户的长期记忆（来自本机记录）：" + "；".join(parts) + "。"
                if parts else "")

    def prompt_line(self) -> str:
        """拼进系统提示词的增量行；无事实时不改变原提示词（保持字节级兼容）。"""
        facts = self.facts()
        return f"\n{facts}" if facts else ""

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

    def clear(self) -> None:
        self._facts = {"user_name": "", "activity": "", "project": ""}
        self._store.save(self._facts)
