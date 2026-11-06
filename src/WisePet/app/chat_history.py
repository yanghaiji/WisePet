"""会话历史门面：把「一条完整对话」事件落到按天仓储，并供 UI 回看当天记录。

ui 不直接调 infra 仓储（AGENT.md 规约 3），组装根订阅事件后转调本类。
"""
from __future__ import annotations

from ..domain.chat_record import ChatRecord, day_key
from ..domain.events import ChatTurnRecordedEvent


class ChatHistory:
    def __init__(self, store):
        self._store = store

    def record(self, event: ChatTurnRecordedEvent) -> ChatRecord | None:
        """空白内容不入历史（取消/空回复不该留一条空泡）。"""
        content = str(event.content or "").strip()
        if not content:
            return None
        record = ChatRecord(role=event.role, content=content, source=event.source,
                            ts=event.ts, session_id=event.session_id)
        self._store.append(record)
        return record

    def today(self) -> list[ChatRecord]:
        return self._store.load(day_key())

    def records_for(self, day: str) -> list[ChatRecord]:
        return self._store.load(day)

    def days(self) -> list[str]:
        return self._store.days()

    def recent_turns(self, max_days: int = 7) -> list[ChatRecord]:
        """最近若干有记录日期的完整对话，按时间正序，供启动时回灌 ChatSession。

        只读、不发布任何事件：恢复是纯内存动作，绝不能把旧记录又写一遍盘。
        只翻最近 max_days 个文件，避免记录攒多了拖慢冷启动（FR v1.10 C16）。
        """
        records: list[ChatRecord] = []
        for day in self._store.days()[-max_days:]:
            records.extend(self._store.load(day))
        return sorted(records, key=lambda r: r.ts or 0.0)
