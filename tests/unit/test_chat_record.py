"""聊天记录值对象与按天归档键（FR v1.8 C13）。"""
from __future__ import annotations

import re

from WisePet.domain.chat_record import ChatRecord, day_key


class TestDayKey:
    def should_format_local_date_when_timestamp_given(self):
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", day_key(1_700_000_000.0))

    def should_group_same_day_and_separate_adjacent_days(self):
        base = 1_700_000_000.0
        assert day_key(base) == day_key(base + 3600)
        assert day_key(base) != day_key(base + 86400 * 2)

    def should_use_current_time_when_no_timestamp(self):
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", day_key())


class TestChatRecord:
    def should_roundtrip_through_dict(self):
        record = ChatRecord(role="user", content="你好", source="user",
                            ts=1_700_000_000.0, session_id="2026-11-05")
        assert ChatRecord.from_dict(record.to_dict()) == record

    def should_tolerate_missing_fields_when_loading_old_file(self):
        record = ChatRecord.from_dict({"role": "assistant", "content": "在呢"})
        assert record.role == "assistant"
        assert record.content == "在呢"
        assert record.ts == 0.0
