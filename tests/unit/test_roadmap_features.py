"""P1/P2 增量：降频策略、系统空闲因果、活泼度、执行历史、记忆时间线。"""
from __future__ import annotations

from dataclasses import replace

from WisePet.app.action_audit import ActionAudit
from WisePet.app.long_term_memory import LongTermMemory
from WisePet.app.quiet_budget import QuietBudget
from WisePet.domain.config_model import AppConfig
from WisePet.domain.product import CONTEXT_REST, BehaviorContext, context_hint
from WisePet.infra.product_store import ActionAuditStore, MemoryStore, QuietStore
from WisePet.infra.telemetry_store import default_payload


class _Clock:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = t

    def now(self) -> float:
        return self.t


# ---------- 降频而非整天静默 ----------

def should_trickle_a_push_through_instead_of_full_silence(tmp_path):
    quiet = replace(AppConfig().quiet, daily_limit=1, overflow_mode="trickle",
                    trickle_every=2)
    cfg = replace(AppConfig(), quiet=quiet)
    budget = QuietBudget(QuietStore(tmp_path), _Clock(), lambda: cfg)

    assert budget.allow("cheer") is True
    assert budget.allow("news") is False       # 第一次拦截
    assert budget.allow("news") is True        # 第二次按降频放行，不再整天静默
    assert budget.usage()["blocked"] == 2


def should_keep_hard_block_as_the_default(tmp_path):
    cfg = replace(AppConfig(), quiet=replace(AppConfig().quiet, daily_limit=1))
    budget = QuietBudget(QuietStore(tmp_path), _Clock(), lambda: cfg)
    assert budget.allow("cheer") is True
    assert all(budget.allow("news") is False for _ in range(4))


def should_validate_overflow_mode_from_hand_edited_json():
    cfg = AppConfig.from_dict({"quiet": {"overflow_mode": "bogus",
                                         "trickle_every": 999}})
    assert cfg.quiet.overflow_mode == "block"
    assert 2 <= cfg.quiet.trickle_every <= 50


# ---------- 系统空闲的因果 ----------

def should_rest_when_the_user_has_been_away_for_ten_minutes():
    assert context_hint(BehaviorContext(hour=14, idle_s=700), False) is CONTEXT_REST
    assert context_hint(BehaviorContext(hour=14, idle_s=10), False) is None


# ---------- 活泼度可配置 ----------

def should_roundtrip_activity_level_with_whitelist():
    cfg = AppConfig.from_dict({"pet": {"activity": "lively"}})
    assert AppConfig.from_dict(cfg.to_dict()).pet.activity == "lively"
    assert AppConfig.from_dict({"pet": {"activity": "chaos"}}).pet.activity == "normal"


# ---------- 执行历史 ----------

def should_keep_recent_action_audit_entries(tmp_path):
    audit = ActionAudit(ActionAuditStore(tmp_path), now_provider=lambda: 1_800_000_000.0)
    audit.record("CreateFileAction", "D:/a.md", "ok")
    audit.record("MakePptAction", "D:/b.pptx", "denied")

    recent = audit.recent()
    assert recent[0]["result"] == "denied"
    assert len(recent) == 2
    audit.clear()
    assert audit.recent() == []


# ---------- 长期记忆时间线 ----------

def should_keep_a_timeline_of_what_was_learned(tmp_path):
    store = MemoryStore(tmp_path)
    memory = LongTermMemory(store, lambda: True)

    assert memory.learn_from("我叫小明") is True
    assert memory.learn_from("最近在写 WisePet") is True

    timeline = memory.timeline()
    assert [item["key"] for item in timeline] == ["user_name", "activity"]
    assert LongTermMemory(store, lambda: True).timeline() == timeline

    memory.clear()
    assert memory.timeline() == []


def should_never_expose_a_timeline_when_memory_is_off(tmp_path):
    memory = LongTermMemory(MemoryStore(tmp_path), lambda: False)
    assert memory.timeline() == []


def should_create_a_fresh_install_id_on_clear(tmp_path):
    payload = default_payload(1_800_000_000.0)
    assert payload["install_id"] and payload["days"] == {}
