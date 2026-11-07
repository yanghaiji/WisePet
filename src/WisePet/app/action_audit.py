"""工具执行历史：让「意图协议 → 本地执行」从 demo 变成可追溯、敢用。

只记类型 / 目标路径 / 结果与时间，不记文件正文，不上传；命令面板可回看最近 5 条。
"""
from __future__ import annotations

import time
from typing import Callable

from ..domain.chat_record import day_key


class ActionAudit:
    def __init__(self, store, now_provider: Callable[[], float] | None = None):
        self._store = store
        self._now = now_provider or time.time

    def record(self, kind: str, target: str, result: str) -> dict:
        entry = {"day": day_key(self._now()), "ts": self._now(),
                 "kind": str(kind)[:40], "target": str(target)[:200],
                 "result": str(result)[:80]}
        self._store.append(entry)
        return entry

    def recent(self, limit: int = 5) -> list[dict]:
        return self._store.load()[-max(0, int(limit)):][::-1]

    def clear(self) -> None:
        self._store.clear()
