"""P1/P2 产品能力回归：场景包 / 成长外观 / 行为因果 / 打扰预算 / 长期记忆 / 权限分级。

全部是纯逻辑 + tmp_path 落盘，不依赖 Qt 显示环境。
"""
from __future__ import annotations

from dataclasses import replace

from WisePet.app.action_protocol import (
    PERMISSION_AUTO, PERMISSION_CONFIRM, PERMISSION_DENY, CreateFileAction,
    MakePptAction, RemindAction, action_preview, permission_required,
)
from WisePet.app.chat_history import ChatHistory
from WisePet.app.long_term_memory import LongTermMemory
from WisePet.app.quiet_budget import QuietBudget
from WisePet.domain.chat_record import ChatRecord, day_key
from WisePet.domain.config_model import AppConfig
from WisePet.domain.product import (
    CONTEXT_REST, CONTEXT_SLEEP, CONTEXT_STRETCH, BehaviorContext,
    apply_scene, context_hint, growth_decoration, is_push_source, scene_ids,
)
from WisePet.infra.product_store import MemoryStore, QuietStore


class _Clock:
    def __init__(self, t: float = 1_700_000_000.0):
        self.t = t

    def now(self) -> float:
        return self.t


class _StubHistoryStore:
    """按天分桶的内存仓储；ChatHistory 只看 days()/load()，不关心落盘格式。"""

    def __init__(self):
        self.by_day: dict[str, list] = {}

    def days(self):
        return sorted(self.by_day)

    def load(self, day):
        return list(self.by_day.get(day, []))

    def append(self, record):
        self.by_day.setdefault(day_key(record.ts), []).append(record)


def _record(content: str, ts: float) -> ChatRecord:
    return ChatRecord(role="user", content=content, source="local", ts=ts)


# ---------- 配置与场景包 ----------

def should_roundtrip_new_product_settings():
    cfg = AppConfig.from_dict({"quiet": {"daily_limit": 7, "per_source_limit": 3,
                                         "muted": True},
                               "behavior": {"context_driven": False},
                               "memory": {"enabled": False},
                               "actions": {"write_permission": "auto"}})
    again = AppConfig.from_dict(cfg.to_dict())

    assert again.quiet.daily_limit == 7 and again.quiet.muted is True
    assert again.behavior.context_driven is False
    assert again.memory.enabled is False
    assert again.actions.write_permission == "auto"


def should_fall_back_to_safe_defaults_for_bad_product_settings():
    cfg = AppConfig.from_dict({"quiet": {"daily_limit": -5, "per_source_limit": 9999},
                               "actions": {"write_permission": "root"}})

    assert cfg.quiet.daily_limit >= 1 and cfg.quiet.per_source_limit <= 100
    assert cfg.actions.write_permission == PERMISSION_CONFIRM
    assert AppConfig().quiet.enabled is True


def should_roundtrip_system_settings():
    cfg = AppConfig.from_dict({"system": {"log_level": "debug", "log_max_mb": 25,
                                          "log_retention_days": 7,
                                          "log_split_by_level": False}})
    again = AppConfig.from_dict(cfg.to_dict())

    assert again.system.log_level == "debug"
    assert again.system.log_max_mb == 25
    assert again.system.log_retention_days == 7
    assert again.system.log_split_by_level is False
    assert AppConfig().system.log_level == "info"


def should_clamp_bad_system_settings_to_safe_defaults():
    cfg = AppConfig.from_dict({"system": {"log_level": "verbose",
                                          "log_max_mb": 9999,
                                          "log_retention_days": -2}})

    assert cfg.system.log_level == "info"
    assert 1 <= cfg.system.log_max_mb <= 100
    assert 1 <= cfg.system.log_retention_days <= 30


def should_apply_every_scene_without_touching_ai_or_persona():
    base = AppConfig.from_dict({
        "ai": {"api_key": "sk-secret", "model": "m1"},
        "persona": {"name": "阿橘"},
        "pet": {"walk_speed_px_per_s": 77.0},
        "quiet": {"daily_limit": 20},
    })
    for scene in scene_ids():
        result = apply_scene(base, scene)
        assert result.ai.api_key == "sk-secret"
        assert result.persona.name == "阿橘"
        assert result.pet.walk_speed_px_per_s == 77.0
        assert result.quiet.daily_limit <= 20
    assert apply_scene(base, "deep_work").news.enabled is False
    assert apply_scene(base, "healthy").reminders.water.interval_min == 30.0
    assert apply_scene(base, "daily_review").chat.idle_hide_s == 60.0


def should_ignore_unknown_scene():
    cfg = AppConfig()
    assert apply_scene(cfg, "no-such-scene") is cfg


# ---------- 成长外观 / 行为因果 ----------

def should_make_growth_visibly_different_by_stage():
    baby, teen, adult = (growth_decoration(1), growth_decoration(4),
                         growth_decoration(10))
    assert baby.scale < teen.scale < adult.scale
    assert not baby.ring and teen.badge == "Lv4" and adult.badge == "Lv10"
    assert not growth_decoration(10, enabled=False).visible


def should_translate_life_context_into_behavior_hint():
    assert context_hint(None, False) is None
    assert context_hint(BehaviorContext(hour=14, focusing=True), False) is CONTEXT_REST
    assert context_hint(BehaviorContext(hour=14, quiet=True), False) is CONTEXT_REST
    assert context_hint(BehaviorContext(hour=2), True) is CONTEXT_SLEEP
    assert context_hint(BehaviorContext(hour=8), False) is CONTEXT_STRETCH
    assert context_hint(BehaviorContext(hour=14), False) is None


def should_only_budget_active_push_sources():
    assert is_push_source("reminder") and is_push_source("cheer")
    assert not is_push_source("chat") and not is_push_source("reply")


# ---------- 权限分级 / 预览 ----------

def should_grade_action_permissions():
    remind = RemindAction(text="喝水", delay_min=5)
    writing = CreateFileAction(path="D:/a.md", content="hello")
    assert permission_required(remind, PERMISSION_DENY) == PERMISSION_AUTO
    assert permission_required(writing, PERMISSION_AUTO) == PERMISSION_AUTO
    assert permission_required(writing, PERMISSION_DENY) == PERMISSION_DENY
    assert permission_required(writing, "bogus") == PERMISSION_CONFIRM


def should_preview_full_write_content_before_confirm():
    preview = action_preview(CreateFileAction(path="D:/a.md", content="hello"))
    assert "D:/a.md" in preview and "hello" in preview
    ppt = MakePptAction(path="D:/a.pptx", title="周会",
                        slides=({"title": "进度", "bullets": ["完成", "待办"]},))
    assert "进度" in action_preview(ppt) and "完成" in action_preview(ppt)


# ---------- 跨日全文检索 ----------

def should_search_across_all_days_case_insensitively():
    store = _StubHistoryStore()
    store.by_day = {
        "2026-11-01": [_record("今天写了 WisePet 的规划", 1.0)],
        "2026-11-05": [_record("wisepet 打包有点慢", 5.0),
                       _record("无关的晚饭", 6.0)],
    }
    history = ChatHistory(store)

    hits = history.search("wisepet")

    assert len(hits) == 2 and "打包" in hits[0].content   # 新的在前
    assert history.search("") == []
    assert len(history.search("wisepet", limit=1)) == 1


# ---------- 打扰预算 ----------

def _budget(tmp_path, cfg=None, clock=None):
    return QuietBudget(QuietStore(tmp_path), clock or _Clock(),
                       lambda: cfg or AppConfig())


def should_enforce_daily_and_per_source_quota(tmp_path):
    cfg = replace(AppConfig(), quiet=replace(AppConfig().quiet,
                                             daily_limit=3, per_source_limit=2))
    budget = _budget(tmp_path, cfg)

    assert budget.allow("cheer") is True
    assert budget.allow("cheer") is True
    assert budget.allow("cheer") is False       # 单来源超额
    assert budget.allow("news") is True
    assert budget.allow("news") is False        # 总量到顶
    assert budget.usage()["total"] == 3


def should_let_user_initiated_speech_through_even_when_muted(tmp_path):
    cfg = replace(AppConfig(), quiet=replace(AppConfig().quiet, muted=True))
    budget = _budget(tmp_path, cfg)

    assert budget.allow("reminder") is False
    assert budget.allow("chat") is True
    assert budget.allow("reply") is True


def should_reset_quota_on_a_new_day(tmp_path):
    cfg = replace(AppConfig(), quiet=replace(AppConfig().quiet, daily_limit=1))
    clock = _Clock()
    budget = _budget(tmp_path, cfg, clock)
    assert budget.allow("cheer") is True
    assert budget.allow("news") is False

    clock.t += 48 * 3600
    assert budget.allow("news") is True


def should_explain_quota_exhaustion_once_per_day(tmp_path):
    cfg = replace(AppConfig(), quiet=replace(AppConfig().quiet, daily_limit=1))
    clock = _Clock()
    budget = _budget(tmp_path, cfg, clock)

    assert budget.allow("cheer") is True
    assert budget.allow("news") is False
    hint = budget.exhaustion_hint()
    assert hint is not None and "额度" in hint
    assert budget.exhaustion_hint() is None      # 当天只提示一次，别变成新的打扰

    clock.t += 48 * 3600
    assert budget.allow("cheer") is True
    assert budget.allow("news") is False
    assert budget.exhaustion_hint() is not None  # 新的一天可以再提示


def should_restore_sane_push_intervals_in_healthy_scene():
    """健康作息要能真正恢复弹窗：鼓励间隔 1 分钟会几分钟内又打满单来源额度。"""
    base = AppConfig.from_dict({"news": {"interval_min": 1.0},
                                "cheer": {"interval_min": 1.0}})

    result = apply_scene(base, "healthy")

    assert result.news.enabled is True and result.cheer.enabled is True
    assert result.news.interval_min >= 10 and result.cheer.interval_min >= 10
    assert result.quiet.daily_limit > 6 and result.quiet.per_source_limit > 2


def should_disable_budget_entirely_when_configured_off(tmp_path):
    cfg = replace(AppConfig(), quiet=replace(AppConfig().quiet, enabled=False,
                                             daily_limit=1))
    budget = _budget(tmp_path, cfg)
    assert all(budget.allow("cheer") for _ in range(5))


# ---------- 长期记忆 ----------

def _memory(tmp_path, enabled=True):
    return LongTermMemory(MemoryStore(tmp_path), lambda: enabled)


def should_learn_and_persist_user_facts(tmp_path):
    memory = _memory(tmp_path)
    assert memory.learn_from("我叫林克，最近在写 WisePet 的规划") is True
    assert memory.learn_from("我的项目是 WisePet") is True
    assert "林克" in memory.facts() and "WisePet" in memory.facts()
    assert memory.prompt_line().startswith("\n")

    reloaded = _memory(tmp_path)
    assert reloaded.values()["user_name"] == "林克"


def should_not_mistake_mood_for_a_name(tmp_path):
    memory = _memory(tmp_path)
    assert memory.learn_from("我是有点累") is False
    assert memory.facts() == ""


def should_hydrate_local_engine_and_respect_opt_out(tmp_path):
    class _Mem:
        user_name = ""
        activity = ""

    class _Engine:
        def __init__(self):
            self.memory = _Mem()

    memory = _memory(tmp_path)
    memory.learn_from("我叫小明，最近在写代码")
    engine = _Engine()
    memory.hydrate(engine)
    assert engine.memory.user_name == "小明"

    off = _memory(tmp_path, enabled=False)
    assert off.facts() == "" and off.learn_from("我叫新名字") is False
    memory.clear()
    assert memory.values()["user_name"] == ""
