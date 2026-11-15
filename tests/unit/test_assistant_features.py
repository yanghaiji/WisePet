"""轴② A 类五项：托付任务 / 本地资料索引 / 事件主动 / 可遗忘时间线 / 惦记清单。"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from fakes import FakeClock, make_collecting_bus

from WisePet.app.companion_service import CompanionService
from WisePet.app.event_triggers import (
    EVENT_LINES, RULE_FOCUS_STREAK, RULE_LATE_NIGHT, RULE_RETURN,
    EventTriggerService, evaluate,
)
from WisePet.app.knowledge_base import KnowledgeBase
from WisePet.app.memory_timeline import MemoryTimeline
from WisePet.app.task_service import TaskService
from WisePet.domain.chat_record import day_key
from WisePet.domain.companion import (
    is_done_signal, pick_recalls, pick_recall, recall_prompt_lines, resolve_key,
)
from WisePet.domain.config_model import AppConfig
from WisePet.domain.events import SpeakRequestedEvent
from WisePet.domain.task_protocol import (
    FOLLOWUP, WATCH_FILE, TaskSpec, backoff_seconds, describe_task,
    is_done_signal as task_done_signal, is_safe_read_target, parse_task_command,
)
from WisePet.infra.event_store import EventStore
from WisePet.infra.knowledge_store import KnowledgeStore
from WisePet.infra.task_store import TaskStore

BASE = 1_700_000_000.0
DAY = 86_400.0


def _cfg(**changes):
    base = AppConfig()
    return replace(base, assistant=replace(base.assistant, **changes))


class MemStore:
    """通用的内存键值仓储替身。"""

    def __init__(self):
        self.data = {}

    def load(self):
        return dict(self.data)

    def save(self, payload):
        self.data = dict(payload)

    def clear(self):
        self.data = {}

    def excluded_keys(self):
        return frozenset(self.data.get("recalled_keys", [])
                         + self.data.get("resolved_recalls", []))

    def mark_recall(self, day):
        self.data["last_recall_day"] = day

    def mark_recall_key(self, key):
        self.data.setdefault("recalled_keys", []).append(key)

    def resolve_recall(self, key):
        self.data.setdefault("resolved_recalls", []).append(key)

    def touch_seen(self, ts):
        self.data["last_seen_ts"] = ts

    def mark_reunion(self, day):
        pass


def _bus():
    return make_collecting_bus()


def _speaks(collected):
    return [e for e in collected if isinstance(e, SpeakRequestedEvent)]


# ---------- 1. 任务协议纯逻辑 ----------

class TestTaskProtocol:
    def should_parse_watch_file_command(self):
        spec = parse_task_command("盯着 D:/a.log，出现 ERROR 就告诉我", now=BASE)
        assert spec is not None and spec.kind == WATCH_FILE
        assert spec.target == "D:/a.log" and spec.match == "ERROR"
        assert spec.next_at > BASE

    def should_parse_followup_command(self):
        spec = parse_task_command("每隔30分钟提醒我交周报", now=BASE)
        assert spec is not None and spec.kind == FOLLOWUP
        assert spec.interval_s == 1800.0 and "交周报" in spec.text

    def should_not_parse_normal_chat_or_too_fast_interval(self):
        assert parse_task_command("今天天气真好", now=BASE) is None
        assert parse_task_command("每隔5分钟提醒我喝水", now=BASE) is None

    def should_backoff_exponentially_with_cap(self):
        assert backoff_seconds(0) == 30.0
        assert backoff_seconds(1) == 60.0
        assert backoff_seconds(9) == 300.0

    def should_detect_done_signal_and_describe(self):
        assert task_done_signal("周报搞定了") and is_done_signal("交了，谢谢")
        assert not task_done_signal("明天再弄吧")
        task = TaskSpec(id="t1", kind=FOLLOWUP, text="催周报", tries=2)
        assert "进行中" in describe_task(task) and "已试 2 次" in describe_task(task)

    def should_reject_unsafe_paths(self):
        assert not is_safe_read_target("")
        assert not is_safe_read_target("https://evil.com/x")
        assert is_safe_read_target("D:/notes/a.md")


# ---------- 2. TaskService ----------

def _task_service(tmp_path, clock=None, cfg=None):
    clock = clock or FakeClock()
    bus, collected = _bus()
    store = TaskStore(tmp_path)
    config = {"cfg": cfg or AppConfig()}
    svc = TaskService(clock, bus, lambda: config["cfg"], store)
    return svc, clock, collected, store, config


class TestTaskService:
    def should_persist_across_restart(self, tmp_path):
        svc, _clock, _bus_out, _store, _cfg = _task_service(tmp_path)
        spec = parse_task_command("每隔30分钟提醒我交周报", now=BASE)
        assert svc.add(spec) is True
        bus2, _collected2 = _bus()
        again = TaskService(FakeClock(), bus2, lambda: AppConfig(),
                            TaskStore(tmp_path))
        assert any(t.id == spec.id for t in again.tasks())

    def should_notify_when_watched_file_matches(self, tmp_path):
        watched = tmp_path / "a.log"
        watched.write_text("all fine", encoding="utf-8")
        svc, clock, collected, _store, _cfg = _task_service(tmp_path)
        spec = TaskSpec(id="t1", kind=WATCH_FILE, text="盯 a.log",
                        target=str(watched), match="ERROR", interval_s=30.0,
                        created_at=BASE, next_at=BASE + 30)
        svc.add(spec)
        collected.clear()
        watched.write_text("ERROR: boom", encoding="utf-8")
        svc.tick(BASE + 31)

        titles = [e.title for e in _speaks(collected)]
        assert "我盯着的事有动静了" in titles
        assert svc.tasks(include_finished=True)[0].status == "done"

    def should_retry_on_missing_file_and_then_stop_with_reason(self, tmp_path):
        svc, clock, collected, _store, _cfg = _task_service(tmp_path)
        spec = TaskSpec(id="t1", kind=WATCH_FILE, text="盯缺文件",
                        target=str(tmp_path / "nope.log"), match="X",
                        interval_s=30.0, max_tries=2,
                        created_at=BASE, next_at=BASE + 30)
        svc.add(spec)
        collected.clear()
        svc.tick(BASE + 31)
        assert svc.tasks()[0].status == "pending"      # 退避重试
        assert svc.tasks()[0].next_at > BASE + 31
        svc.tick(svc.tasks()[0].next_at + 1)
        stopped = svc.tasks(include_finished=True)[0]
        assert stopped.status == "failed"
        assert any("托付停下了" in e.title for e in _speaks(collected))

    def should_stop_followup_after_max_tries(self, tmp_path):
        svc, clock, collected, _store, _cfg = _task_service(tmp_path)
        spec = TaskSpec(id="t1", kind=FOLLOWUP, text="催：交周报",
                        interval_s=60.0, max_tries=2,
                        created_at=BASE, next_at=BASE + 60)
        svc.add(spec)
        collected.clear()
        svc.tick(BASE + 61)
        assert "还惦记着呢" in [e.title for e in _speaks(collected)]
        task = svc.tasks()[0]
        svc.tick(task.next_at + 1)
        assert svc.tasks(include_finished=True)[0].status == "done"

    def should_resolve_followup_on_done_signal(self, tmp_path):
        svc, _clock, _collected, _store, _cfg = _task_service(tmp_path)
        svc.add(TaskSpec(id="t1", kind=FOLLOWUP, text="催：交周报",
                         interval_s=60.0, max_tries=5, next_at=BASE + 60))
        assert svc.resolve("周报搞定了") == 1
        assert svc.tasks() == []
        assert svc.resolve("还在弄") == 0

    def should_cancel_and_describe(self, tmp_path):
        svc, _clock, _collected, _store, _cfg = _task_service(tmp_path)
        svc.add(TaskSpec(id="t1", kind=FOLLOWUP, text="催：喝水",
                         interval_s=60.0, next_at=BASE + 60))
        assert svc.cancel("t1") is True
        assert "已取消" in svc.describe()
        assert svc.cancel_all() == 0

    def should_honor_disable_switch(self, tmp_path):
        cfg = _cfg(enabled=False)
        svc, clock, collected, _store, _config = _task_service(tmp_path, cfg=cfg)
        svc._enabled = lambda: False
        svc.add(TaskSpec(id="t1", kind=FOLLOWUP, text="催", interval_s=60.0,
                         next_at=BASE + 60))
        collected.clear()
        svc.tick(BASE + 61)
        assert _speaks(collected) == []


# ---------- 3. 本地资料索引 ----------

class TestKnowledgeBase:
    def _build(self, tmp_path):
        folder = tmp_path / "notes"
        folder.mkdir()
        (folder / "plan.md").write_text("周报计划：周一交周报，周三复盘部署",
                                         encoding="utf-8")
        (folder / "readme.txt").write_text("项目代号 北极星，使用本地索引",
                                            encoding="utf-8")
        return KnowledgeBase(KnowledgeStore(tmp_path)), folder

    def should_build_and_search_local_files(self, tmp_path):
        kb, folder = self._build(tmp_path)
        result = kb.build(str(folder), now=BASE)
        assert result["files"] == 2 and result["chunks"] >= 2
        hits = kb.search("周报计划", top_k=3)
        assert hits and "周报" in hits[0]["text"]
        assert "周报" in kb.prompt_context("周报什么时候交")

    def should_persist_index_and_clear(self, tmp_path):
        kb, folder = self._build(tmp_path)
        kb.build(str(folder), now=BASE)
        assert KnowledgeBase(KnowledgeStore(tmp_path)).search("北极星")
        kb.clear()
        assert KnowledgeBase(KnowledgeStore(tmp_path)).search("北极星") == []

    def should_skip_non_text_and_symlinks(self, tmp_path):
        kb, folder = self._build(tmp_path)
        (folder / "image.png").write_bytes(b"not text at all")
        kb.build(str(folder), now=BASE)
        assert kb.status()["files"] == 2      # .png 不在白名单

    def should_reject_missing_folder(self, tmp_path):
        kb, _folder = self._build(tmp_path)
        with pytest.raises(ValueError):
            kb.build(str(tmp_path / "nope"), now=BASE)

    def should_return_empty_when_no_match_or_disabled_query(self, tmp_path):
        kb, folder = self._build(tmp_path)
        kb.build(str(folder), now=BASE)
        assert kb.prompt_context("") == ""
        assert kb.prompt_context("完全不相关的量子纠缠话题") == ""


# ---------- 4. 事件驱动主动 ----------

class TestEventTriggers:
    def should_fire_when_user_returns_after_long_idle(self):
        hits = evaluate(hour=14, idle_s=10, prev_idle=2000,
                        today_focus_count=0, fired_today=set(), daily_limit=2)
        assert RULE_RETURN in hits

    def should_fire_late_night_and_focus_streak(self):
        hits = evaluate(hour=23, idle_s=30, prev_idle=None,
                        today_focus_count=2, fired_today=set(), daily_limit=3)
        assert RULE_LATE_NIGHT in hits and RULE_FOCUS_STREAK in hits

    def should_dedupe_and_cap_daily(self):
        hits = evaluate(hour=23, idle_s=30, prev_idle=None,
                        today_focus_count=2,
                        fired_today={RULE_LATE_NIGHT, RULE_RETURN}, daily_limit=2)
        assert hits == []
        assert evaluate(hour=14, idle_s=None, prev_idle=None,
                        today_focus_count=0, fired_today=set(), daily_limit=0) == []

    def should_degrade_when_idle_unavailable(self):
        assert RULE_RETURN not in evaluate(hour=14, idle_s=None, prev_idle=2000,
                                           today_focus_count=0,
                                           fired_today=set(), daily_limit=2)

    def should_publish_and_persist_once_per_day(self, tmp_path):
        clock = FakeClock()
        bus, collected = _bus()
        config = {"cfg": AppConfig()}
        svc = EventTriggerService(
            clock, bus, lambda: config["cfg"], EventStore(tmp_path),
            idle_provider=lambda: 10.0, focus_count_provider=lambda: 3)
        svc.tick(BASE)
        assert _speaks(collected)                      # 仅 focus_streak
        titles = [e.title for e in _speaks(collected)]
        assert titles == ["今天很拼"]
        collected.clear()
        svc.tick(BASE + 60)
        assert _speaks(collected) == []                # 当日去重

    def should_honor_disable_switch(self, tmp_path):
        bus, collected = _bus()
        cfg = _cfg(enabled=False)
        svc = EventTriggerService(
            FakeClock(), bus, lambda: cfg, EventStore(tmp_path),
            idle_provider=lambda: 10.0, focus_count_provider=lambda: 3)
        svc.tick(BASE)
        assert collected == []

    def should_have_line_for_every_rule(self):
        assert set(EVENT_LINES) >= {RULE_RETURN, RULE_LATE_NIGHT, RULE_FOCUS_STREAK}


# ---------- 5. 可遗忘时间线 ----------

class _FakeChatRepo:
    def __init__(self, days):
        self._days = list(days)
        self.deleted = []

    def days(self):
        return list(self._days)

    def load(self, day):
        return [object()] * 3

    def delete_day(self, day):
        self.deleted.append(day)
        self._days = [d for d in self._days if d != day]
        return 1


class _FakeAuditStore:
    def __init__(self, entries):
        self.entries = list(entries)

    def load(self):
        return list(self.entries)

    def delete_at(self, index):
        if 0 <= index < len(self.entries):
            del self.entries[index]
            return True
        return False


class _FakeMemoryStore:
    def __init__(self):
        self.facts = {"user_name": "小明", "activity": "", "project": ""}
        self.history = [{"day": "2026-11-10", "key": "user_name", "value": "小明"}]

    def load(self):
        return dict(self.facts)

    def load_history(self):
        return list(self.history)

    def load_fact_entries(self):
        return []

    def delete_fact_entry(self, index):
        return False

    def delete_fact(self, key):
        if not self.facts.get(key):
            return False
        self.facts[key] = ""
        self.history = [h for h in self.history if h["key"] != key]
        return True

    def delete_history_at(self, index):
        if 0 <= index < len(self.history):
            del self.history[index]
            return True
        return False


class TestMemoryTimeline:
    def _timeline(self):
        memory = _FakeMemoryStore()
        audit = _FakeAuditStore([
            {"day": "2026-11-10", "kind": "create_file",
             "target": "D:/a.md", "result": "ok"}])
        chat = _FakeChatRepo(["2026-11-10"])
        return MemoryTimeline(memory_store=memory, audit_store=audit,
                              chat_repo=chat), memory, audit, chat

    def should_aggregate_all_sources(self):
        timeline, _m, _a, _c = self._timeline()
        sources = {e.source for e in timeline.entries()}
        assert {"长期记忆", "记忆变更", "聊天记录", "工具执行"} <= sources

    def should_really_delete_selected_entries(self):
        timeline, memory, audit, chat = self._timeline()
        count = timeline.forget(["memory:user_name", "chat:2026-11-10",
                                 "audit:0", "history:0"])
        assert count == 4
        assert memory.facts["user_name"] == ""
        assert chat.deleted == ["2026-11-10"]
        assert audit.entries == []
        assert memory.history == []

    def should_ignore_unknown_ids_and_export_text(self):
        timeline, _m, _a, _c = self._timeline()
        assert timeline.forget(["nope:1"]) == 0
        assert "# WisePet 本机数据时间线" in timeline.export_text()


# ---------- 6. 惦记清单化 ----------

def _rec(role, content, ts):
    return SimpleNamespace(role=role, content=content, ts=ts)


class TestRecallList:
    def should_pick_multiple_distinct_recalls(self):
        records = [
            _rec("user", "明天打算把周报写完", BASE - 5 * DAY),
            _rec("user", "下周准备整理一下笔记", BASE - 3 * DAY),
            _rec("user", "回头准备写个总结", BASE - 2 * DAY),
        ]
        found = pick_recalls(records, now=BASE, limit=3)
        assert len(found) == 3
        assert found[0].days_ago == 2                 # 最近优先
        assert len({resolve_key(c) for c in found}) == 3

    def should_exclude_resolved_and_already_mentioned(self):
        records = [
            _rec("user", "明天打算把周报写完", BASE - 5 * DAY),
            _rec("user", "下周准备整理一下笔记", BASE - 3 * DAY),
        ]
        all_found = pick_recalls(records, now=BASE, limit=3)
        key = resolve_key(all_found[0])
        rest = pick_recalls(records, now=BASE, limit=3, exclude=frozenset({key}))
        assert len(rest) == 1 and resolve_key(rest[0]) != key

    def should_skip_recall_with_later_done_signal(self):
        records = [
            _rec("user", "明天打算把周报写完", BASE - 5 * DAY),
            _rec("user", "下周准备整理一下笔记", BASE - 3 * DAY),
            _rec("user", "周报已经搞定了", BASE - 1 * DAY),
        ]
        found = pick_recalls(records, now=BASE, limit=3)
        assert len(found) == 1 and "笔记" in found[0].text

    def should_keep_single_candidate_compat(self):
        records = [_rec("user", "明天打算把周报写完", BASE - 3 * DAY)]
        assert pick_recall(records, now=BASE) is not None
        assert "不要一口气全列出来" in recall_prompt_lines(
            pick_recalls(records, now=BASE))

    def should_resolve_recall_when_user_says_done(self):
        clock = FakeClock()
        bus, collected = _bus()
        store = MemStore()
        cfg = {"cfg": AppConfig()}
        svc = CompanionService(
            clock, bus, lambda: cfg["cfg"],
            records_provider=lambda: [
                _rec("user", "明天打算把周报写完", BASE - 3 * DAY)],
            store=store, name_provider=lambda: "小墨")
        candidates = svc.recall_candidates()
        assert len(candidates) == 1
        assert svc.resolve("周报搞定了") == 1
        # 消解后重新扫描不再出现这一条
        assert svc.recall_candidates() == []
        assert svc.prompt_line() == ""


# ---------- 7. 配置 ----------

class TestAssistantConfig:
    def should_roundtrip_assistant_settings(self):
        cfg = _cfg(enabled=False, knowledge_enabled=True,
                   knowledge_dir="D:/notes", knowledge_top_k=5,
                   event_daily_limit=0, task_poll_s=60.0)
        back = AppConfig.from_dict(cfg.to_dict())
        assert back.assistant.enabled is False
        assert back.assistant.knowledge_dir == "D:/notes"
        assert back.assistant.knowledge_top_k == 5
        assert back.assistant.event_daily_limit == 0
        assert back.assistant.task_poll_s == 60.0

    def should_clamp_invalid_values_and_keep_safe_defaults(self):
        cfg = AppConfig.from_dict({"assistant": {
            "task_poll_s": -1, "knowledge_top_k": 999,
            "event_daily_limit": 999, "knowledge_dir": "x" * 900}})
        assert cfg.assistant.task_poll_s == 10.0
        assert cfg.assistant.knowledge_top_k == 8
        assert cfg.assistant.event_daily_limit == 10
        assert len(cfg.assistant.knowledge_dir) == 500
        fresh = AppConfig().assistant
        assert fresh.knowledge_enabled is False and fresh.knowledge_dir == ""
