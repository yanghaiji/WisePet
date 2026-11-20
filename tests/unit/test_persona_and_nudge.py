"""人格锚与指标关怀回归（FR 功能 2/3）。"""
from __future__ import annotations

from WisePet.app.care_nudge import nudge_line, should_nudge as _should_nudge
from WisePet.app.telemetry_service import compute_metrics
from WisePet.domain.config_model import AppConfig
from WisePet.domain.persona_anchor import anchor_prompt


# ---------- 人格锚 ----------

def test_anchor_prompt_differs_by_skin():
    cat = anchor_prompt("cat")
    dog = anchor_prompt("dog")
    assert "说话风格锚点" in cat and "用户：" in cat and "你：" in cat
    assert cat != dog                       # 猫狗语气样例不同
    assert "蜷" in cat or "打了个盹" in cat  # 猫的高冷样例


def test_anchor_prompt_respects_enabled_and_unknown_skin():
    assert anchor_prompt("cat", enabled=False) == ""
    assert anchor_prompt("unknown-skin")      # 未知皮肤回落默认猫样例


def test_persona_anchor_config_roundtrip():
    cfg = AppConfig.from_dict({"persona": {"anchor_enabled": False}})
    assert cfg.persona.anchor_enabled is False
    assert AppConfig.from_dict(cfg.to_dict()).persona.anchor_enabled is False
    assert AppConfig().persona.anchor_enabled is True


# ---------- 指标关怀 ----------

def test_should_nudge_thresholds():
    assert _should_nudge(None) is False      # 从未互动不打扰
    assert _should_nudge(0) is False
    assert _should_nudge(2) is False
    assert _should_nudge(3) is True
    assert _should_nudge(9) is True


def test_nudge_line_by_duration():
    short = nudge_line(3, "小墨")
    long = nudge_line(9, "小墨")
    assert "小墨" in short and "今天还好吗" in short
    assert "想你" in long


def test_metrics_days_since_last_engagement():
    data = {
        "first_seen": "2026-11-01",
        "days": {
            "2026-11-01": {"msg:sent": 2},        # 有效互动
            "2026-11-05": {"app_start": 1},       # 被动，不算
            "2026-11-15": {"tool:used": 1},       # 最近一次有效互动
        },
    }
    metrics = compute_metrics(data, "2026-11-18")
    assert metrics["days_since_last_engagement"] == 3
    assert metrics["engaged_days"] == 2
    assert _should_nudge(metrics["days_since_last_engagement"]) is True

    # 从未有效互动
    data2 = {"days": {"2026-11-17": {"app_start": 1}}}
    assert compute_metrics(data2, "2026-11-18")["days_since_last_engagement"] is None
