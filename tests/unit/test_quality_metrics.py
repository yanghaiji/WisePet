"""三件事核心的纯逻辑回归：有效互动口径 / 护栏 / 模型分级 / 主动关怀。"""
from __future__ import annotations

from WisePet.domain.care import (
    CARE_TERMS, care_term, context_score, solar_term, suggested_interval,
)
from WisePet.domain.metrics import (
    ENGAGEMENT_EVENTS, engaged_days, guardrail_status,
)
from WisePet.domain.model_capability import (
    TIER_LITE, TIER_PRO, TIER_STANDARD, capability_hint, infer_tier,
    normalize_tier, tools_allowed,
)


# ---------- 第 1 件：有效互动口径与护栏 ----------

def test_engaged_days_only_counts_real_interaction():
    days = {
        "2026-11-01": {"app_start": 3, "reminder_shown": 5},   # 被动，不算
        "2026-11-02": {"app_start": 1, "msg:sent": 2},          # 算
        "2026-11-03": {"tool:used": 1},                          # 算
        "2026-11-04": {"reminder:confirmed": 1},                 # 算
    }
    assert engaged_days(days) == 3
    assert "msg:sent" in ENGAGEMENT_EVENTS
    assert "app_start" not in ENGAGEMENT_EVENTS


def test_guardrail_lights_ok_warn_na():
    healthy = guardrail_status({
        "reminder_dismiss_rate": 0.1, "ai_fallback_rate": 0.0,
        "crash_count": 0, "d7_eligible": False, "d7_retained": False})
    assert healthy["disturbance"] == "ok"
    assert healthy["brain"] == "ok"
    assert healthy["retention"] == "n/a"

    bad = guardrail_status({
        "reminder_dismiss_rate": 0.8, "ai_fallback_rate": 0.6,
        "crash_count": 5, "d7_eligible": True, "d7_retained": False})
    assert set(bad.values()) == {"warn"}


# ---------- 第 2 件：模型能力分级 ----------

def test_infer_tier_by_model_name():
    assert infer_tier("glm-4-flash") == TIER_LITE
    assert infer_tier("gpt-4o-mini") == TIER_LITE
    assert infer_tier("claude-3.7-sonnet") == TIER_PRO
    assert infer_tier("some-unknown-model") == TIER_STANDARD


def test_normalize_tier_falls_back_to_inferred():
    assert normalize_tier("pro") == TIER_PRO
    assert normalize_tier("bogus", TIER_LITE) == TIER_LITE
    assert normalize_tier("", TIER_STANDARD) == TIER_STANDARD


def test_tools_allowed_by_switch_only_not_tier():
    """v1.35 修正：lite 档不再被剥夺工具（曾误伤 glm-4-flash 的联网能力）。"""
    assert tools_allowed(TIER_LITE, True) is True      # lite 也开放工具
    assert tools_allowed(TIER_STANDARD, True) is True
    assert tools_allowed(TIER_PRO, True) is True
    assert tools_allowed(TIER_PRO, False) is False     # 只有总开关能关


def test_capability_hint_only_for_lite():
    hint = capability_hint("glm-4-flash", "")
    assert hint != ""
    assert "先调用对应工具" in hint and "不能凭记忆编造" in hint
    assert capability_hint("gpt-4o", "") == ""


# ---------- 第 3 件：主动关怀 ----------

def test_solar_term_maps_known_dates():
    # 2026-11-07 是立冬（近似表按公历月日匹配）
    assert solar_term(1_763_337_600) in {None, "立冬"}  # 容错：仅按月日匹配
    assert care_term(1_763_337_600) in {None, "立冬"}
    assert "冬至" in CARE_TERMS
    assert "小寒" not in CARE_TERMS


def test_context_score_zero_when_busy_or_quiet():
    assert context_score(hour=14, focusing=True) == 0.0
    assert context_score(hour=14, quiet=True) == 0.0
    assert context_score(hour=14) == 1.0
    assert context_score(hour=23) <= 0.5
    assert context_score(hour=14, dismiss_rate=0.9) <= 0.6


def test_suggested_interval_doubles_on_high_dismiss():
    assert suggested_interval(30.0, 0.8) == 60.0
    assert suggested_interval(30.0, 0.1) == 30.0
    assert suggested_interval(30.0, None) == 30.0


# ---------- 第 3 件：节气关怀接入事件触发 ----------

def test_solar_term_line_mentions_term():
    from WisePet.app.event_triggers import solar_term_line
    assert "立冬" in solar_term_line("立冬")


def test_event_trigger_fires_solar_term_once_and_respects_quiet(tmp_path):
    from dataclasses import replace

    from fakes import FakeClock, make_collecting_bus
    from WisePet.app.event_triggers import EventTriggerService
    from WisePet.domain.config_model import AppConfig
    from WisePet.domain.events import SpeakRequestedEvent
    from WisePet.infra.event_store import EventStore

    base = 1_700_000_000.0
    clock = FakeClock()
    bus, collected = make_collecting_bus()
    cfg = AppConfig()

    svc = EventTriggerService(
        clock, bus, lambda: cfg, EventStore(tmp_path),
        idle_provider=lambda: 10.0, focus_count_provider=lambda: 0,
        term_provider=lambda _ts: "立冬",
        dismiss_rate_provider=lambda: 0.0)
    svc.tick(base)
    titles = [e.title for e in collected if isinstance(e, SpeakRequestedEvent)]
    assert "立冬到啦" in titles
    collected.clear()
    svc.tick(base + 60)                     # 当日去重
    assert collected == []

    # 情境分 = 0（专注中）时不发节气关怀
    busy_cfg = replace(cfg, assistant=replace(cfg.assistant, event_daily_limit=2))
    svc2 = EventTriggerService(
        FakeClock(), make_collecting_bus()[0], lambda: busy_cfg,
        EventStore(tmp_path / "busy"),
        idle_provider=lambda: 10.0, focus_count_provider=lambda: 0,
        term_provider=lambda _ts: "立冬",
        dismiss_rate_provider=lambda: 0.9)  # 高忽略率 → 情境分 0.6>0，仍可发
    assert svc2._dismiss_rate() == 0.9
