"""会话历史门面：领域事件 → 只追加记录 / 按天回看（FR v1.8 C13）。"""
from __future__ import annotations

import time

from WisePet.app.chat_history import ChatHistory
from WisePet.domain.chat_record import ChatRecord, day_key
from WisePet.domain.events import ChatTurnRecordedEvent


class StubStore:
    def __init__(self):
        self.records: list[ChatRecord] = []

    def append(self, record):
        self.records.append(record)

    def load(self, day):
        return [r for r in self.records if day_key(r.ts) == day]

    def days(self):
        return sorted({day_key(r.ts) for r in self.records})


def _event(role="user", content="你好", source="user", ts=1_700_000_000.0):
    return ChatTurnRecordedEvent(role=role, content=content, source=source,
                                 ts=ts, session_id=day_key(ts))


def should_append_record_when_turn_event_received():
    store = StubStore()
    history = ChatHistory(store)
    record = history.record(_event())

    assert record.content == "你好" and record.role == "user"
    assert store.records == [record]


def should_ignore_blank_turn_when_reply_empty():
    store = StubStore()
    history = ChatHistory(store)

    assert history.record(_event(role="assistant", content="   ")) is None
    assert store.records == []


def should_return_today_records_from_store():
    store = StubStore()
    history = ChatHistory(store)
    history.record(_event(ts=time.time()))

    assert [r.content for r in history.today()] == ["你好"]
    assert history.days() == [day_key()]


def should_expose_records_for_specific_day():
    store = StubStore()
    history = ChatHistory(store)
    history.record(_event())

    assert history.records_for(day_key(1_700_000_000.0)) == store.records


def should_return_recent_turns_in_chronological_order():
    store = StubStore()
    history = ChatHistory(store)
    history.record(_event(content="第三句", ts=1_700_000_300.0))
    history.record(_event(content="第一句", ts=1_700_000_100.0))
    history.record(_event(content="第二句", ts=1_700_000_200.0))

    assert [r.content for r in history.recent_turns()] == ["第一句", "第二句", "第三句"]


def should_skip_blank_and_system_records_when_reading_recent():
    store = StubStore()
    history = ChatHistory(store)
    history.record(_event(content="正常", ts=time.time()))
    store.records.append(ChatRecord(role="system", content="系统提示", ts=time.time()))
    store.records.append(ChatRecord(role="assistant", content="  ", ts=time.time()))

    # 门面只负责按时间取回，过滤（system/兜底/空白）在组装根回灌时做
    assert any(r.content == "正常" for r in history.recent_turns())
