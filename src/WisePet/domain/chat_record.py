"""聊天记录值对象与按天归档键（domain 层零三方依赖）。

与 `ChatSession` 分工：session 是「发给模型的上下文窗口」，会按 max_turns 主动删最早；
这里是「聊天记录」，只追加不截断，按本地日期分文件保存，供会话窗回看。
"""
from __future__ import annotations

import time
from dataclasses import dataclass

#: 来源口径与日志一致：ai=远端模型 / local=本地小脑 / fallback=兜底话术 / user=用户
VALID_SOURCES = ("ai", "local", "fallback", "user", "system", "")


def day_key(ts: float | None = None) -> str:
    """本地时区的 YYYY-MM-DD；按天归档与按天会话 ID 的单一真相源。"""
    return time.strftime("%Y-%m-%d", time.localtime(time.time() if ts is None else ts))


@dataclass(frozen=True)
class ChatRecord:
    """一条完整对话记录（用户消息或一条完整回复，不是流式增量）。"""

    role: str                  # user / assistant / system
    content: str
    source: str = ""
    ts: float = 0.0
    session_id: str = ""

    def to_dict(self) -> dict:
        return {"role": self.role, "content": self.content, "source": self.source,
                "ts": self.ts, "session_id": self.session_id}

    @classmethod
    def from_dict(cls, data: dict) -> "ChatRecord":
        return cls(role=str(data.get("role") or ""),
                   content=str(data.get("content") or ""),
                   source=str(data.get("source") or ""),
                   ts=float(data.get("ts") or 0.0),
                   session_id=str(data.get("session_id") or ""))
