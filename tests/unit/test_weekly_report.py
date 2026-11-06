"""WeeklyReportService 测试：本地统计、话题提取、到点推送与「一周一次」去重。"""
import time

from WisePet.app.nlp import Tokenizer, bigram_split
from WisePet.app.weekly_report import (
    WeeklyReportService,
    _week_id,
    local_report,
    summarize_week,
)
from WisePet.domain.chat_record import ChatRecord
from WisePet.domain.config_model import AppConfig, WeeklySettings
from WisePet.domain.events import SpeakRequestedEvent
from fakes import BASE_TS, FakeClock, make_collecting_bus


class MemStore:
    def __init__(self, marker=None):
        self._marker = dict(marker or {})

    def load(self):
        return dict(self._marker)

    def save(self, marker):
        self._marker = dict(marker)


def _rec(content, role="user", day="2026-11-01", ts=None):
    return ChatRecord(role=role, content=content, source="user",
                      ts=ts if ts is not None else BASE_TS, session_id=day)


def _wait(collected, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not collected:
        time.sleep(0.01)


class TestSummarize:
    def should_count_messages_days_and_topic(self):
        tokenizer = Tokenizer(bigram_split)
        records = [_rec("喝水", day="2026-11-01"), _rec("喝水", day="2026-11-01"),
                   _rec("喝水", day="2026-11-02"), _rec("你好", role="assistant")]
        stats = summarize_week(records, tokenizer)
        assert stats["messages"] == 4
        assert stats["active_days"] == 2
        assert stats["top_topic"] == "喝水"

    def should_leave_topic_empty_when_no_repetition(self):
        stats = summarize_week([_rec("吃饭")], Tokenizer(bigram_split))
        assert stats["top_topic"] == ""

    def should_handle_empty_week(self):
        stats = summarize_week([], Tokenizer(bigram_split))
        assert stats["messages"] == 0 and stats["top_topic"] == ""


class TestLocalReport:
    def should_mention_counts_when_active(self):
        text = local_report({"messages": 12, "active_days": 5, "top_topic": "喝水"})
        assert "12" in text and "5" in text and "喝水" in text

    def should_be_gentle_when_quiet(self):
        assert local_report({"messages": 0, "active_days": 0, "top_topic": ""})


def _due_config():
    lt = time.localtime(BASE_TS)
    return AppConfig(weekly=WeeklySettings(enabled=True, weekday=lt.tm_wday,
                                           hour=lt.tm_hour))


class TestService:
    def _svc(self, cfg, records, store, bus_collected):
        bus, collected = bus_collected
        svc = WeeklyReportService(FakeClock(), bus, lambda: cfg,
                                  records_provider=lambda: records, store=store,
                                  tokenizer=Tokenizer(bigram_split))
        return svc, collected

    def should_publish_and_mark_when_due(self):
        store = MemStore()
        svc, collected = self._svc(_due_config(), [_rec("喝水"), _rec("喝水")],
                                   store, make_collecting_bus())
        svc.tick()
        _wait(collected)
        speaks = [e for e in collected if isinstance(e, SpeakRequestedEvent)]
        assert speaks and speaks[0].title == "本周回忆录"
        assert store.load().get("last_week") == _week_id(BASE_TS)

    def should_not_resend_within_same_week(self):
        store = MemStore({"last_week": _week_id(BASE_TS)})
        svc, collected = self._svc(_due_config(), [_rec("x")], store,
                                   make_collecting_bus())
        svc.tick()
        time.sleep(0.05)
        assert collected == []

    def should_skip_when_disabled(self):
        cfg = AppConfig(weekly=WeeklySettings(enabled=False))
        svc, collected = self._svc(cfg, [_rec("x")], MemStore(), make_collecting_bus())
        svc.tick()
        time.sleep(0.05)
        assert collected == []

    def should_skip_on_wrong_weekday(self):
        lt = time.localtime(BASE_TS)
        wrong = (lt.tm_wday + 1) % 7
        cfg = AppConfig(weekly=WeeklySettings(enabled=True, weekday=wrong,
                                              hour=lt.tm_hour))
        svc, collected = self._svc(cfg, [_rec("x")], MemStore(), make_collecting_bus())
        svc.tick()
        time.sleep(0.05)
        assert collected == []
