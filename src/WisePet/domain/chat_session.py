"""ChatSession 聚合：会话历史与截断（PRD M4 / 不变量 C1/C2）。"""
from __future__ import annotations

from dataclasses import dataclass

VALID_ROLES = ("user", "assistant")
DEFAULT_MAX_TURNS = 20


@dataclass(frozen=True)
class Turn:
    role: str
    content: str


class ChatSession:
    """会话聚合根。max_turns 表示保留的用户轮次数，历史上限为 max_turns*2 条。"""

    def __init__(self, session_id: str = "default", max_turns: int = DEFAULT_MAX_TURNS):
        if max_turns < 1:
            raise ValueError(f"max_turns must be >= 1, got {max_turns}")
        self.session_id = session_id
        self.max_turns = max_turns
        self.turns: list[Turn] = []

    def add_turn(self, role: str, content: str) -> Turn:
        if role not in VALID_ROLES:
            raise ValueError(f"invalid role: {role!r}, expected one of {VALID_ROLES}")
        text = str(content or "").strip()
        if not text:  # 不变量 C2
            raise ValueError("message content must not be blank")
        turn = Turn(role, text)
        self.turns.append(turn)
        self._truncate()
        return turn

    def _truncate(self) -> None:
        """不变量 C1：超出 max_turns*2 时截断最早历史。"""
        cap = self.max_turns * 2
        if len(self.turns) > cap:
            del self.turns[:len(self.turns) - cap]

    def build_messages(self, system_prompt: str) -> list[dict]:
        """组装发往 AI 网关的 messages（system 恒定第一条，PRD C3）。"""
        return [{"role": "system", "content": system_prompt}] + [
            {"role": t.role, "content": t.content} for t in self.turns
        ]

    @property
    def last_role(self) -> str | None:
        return self.turns[-1].role if self.turns else None
