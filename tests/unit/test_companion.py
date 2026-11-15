"""陪伴轴①：主动引用 / 情境化 / 可干预 / 久别重逢（纯逻辑 + 服务 + 控制器）。"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from WisePet.app.companion_service import CompanionService, RECALL_DELAY_S
from WisePet.app.focus_service import FocusService
from WisePet.domain.chat_record import day_key
from WisePet.domain.companion import (
    context_phrase, pick_recall, recall_line, recall_prompt_line,
    reunion_greeting, reunion_hint,
)
from WisePet.domain.config_model import AppConfig
from WisePet.domain.events import SpeakRequestedEvent
from WisePet.domain.product import (
    CONTEXT_ENERGIZE, CONTEXT_REST, CONTEXT_SLEEP, CONTEXT_STRETCH,
    PUSH_TITLES, BehaviorContext, context_hint,
)
from WisePet.infra.presence_store import PresenceStore
from fakes import FakeClock, make_collecting_bus

DAY = 86_400.0
BASE = 1_700_000_000.0


def _rec(role: str, content: str, ts: float):
    return SimpleNamespace(role=role, content=content, ts=ts)


class MemPresence:
    """PresenceStore 的内存替身（保持同一组方法签名）。"""

    def __init__(self, last_seen_ts: float = 0.0, last_recall_day: str = ""):
        self.data = {"last_seen_ts": last_seen_ts,
                     "last_recall_day": last_recall_day,
                     "last_reunion_day": ""}

    def load(self) -> dict:
        return dict(self.data)

    def touch_seen(self, ts: float) -> None:
        self.data["last_seen_ts"] = ts

    def mark_recall(self, day: str) -> None:
        self.data["last_recall_day"] = day

    def mark_reunion(self, day: str) -> None:
        self.data["last_reunion_day"] = day


def _service(records=None, *, now=BASE, last_seen_ts=0.0, last_recall_day="",
             cfg: AppConfig | None = None, clock: FakeClock | None = None):
    clock = clock or FakeClock()
    clock.advance_to(now) if hasattr(clock, "advance_to") else None
    # FakeClock 只有 advance()；用 advance(delta) 把时间推到目标
    if clock.now() < now:
        clock.advance(now - clock.now())
    bus, collected = make_collecting_bus()
    store = MemPresence(last_seen_ts, last_recall_day)
    config = {"cfg": cfg or AppConfig()}

    def provider():
        return config["cfg"]

    svc = CompanionService(clock, bus, provider,
                           records_provider=lambda: list(records or ()),
                           store=store, name_provider=lambda: "小墨")
    return svc, clock, collected, store, config


# ---------- 主动引用：候选抽取 ----------

class TestPickRecall:
    def should_pick_recent_unfulfilled_remark(self):
        records = [_rec("user", "明天打算把周报写完", BASE - 3 * DAY),
                   _rec("assistant", "好呀，我记着呢", BASE - 3 * DAY + 1)]
        found = pick_recall(records, now=BASE)
        assert found is not None and found.days_ago == 3
        assert "周报" in found.snippet
        assert "一直记着" in recall_line(found)
        assert "周报" in recall_prompt_line(found)

    def should_not_recall_what_was_just_said(self):
        records = [_rec("user", "明天打算把周报写完", BASE - 60)]
        assert pick_recall(records, now=BASE) is None

    def should_not_recall_commands(self):
        records = [_rec("user", "5分钟后提醒我去开会", BASE - 2 * DAY),
                   _rec("user", "帮我写个文件放到桌面", BASE - 2 * DAY)]
        assert pick_recall(records, now=BASE) is None

    def should_not_recall_when_a_later_completion_signal_exists(self):
        records = [_rec("user", "明天打算把周报写完", BASE - 3 * DAY),
                   _rec("user", "周报已经搞定了，谢谢", BASE - 1 * DAY)]
        assert pick_recall(records, now=BASE) is None

    def should_ignore_older_records_outside_window(self):
        records = [_rec("user", "下周准备整理一下笔记", BASE - 40 * DAY)]
        assert pick_recall(records, now=BASE, lookback_days=14) is None

    def should_pick_the_newest_candidate(self):
        records = [_rec("user", "明天打算整理旧笔记", BASE - 6 * DAY),
                   _rec("user", "下周准备整理新笔记", BASE - 2 * DAY)]
        assert "新笔记" in pick_recall(records, now=BASE).snippet

    def should_accept_dicts_and_generators(self):
        records = ({"role": "user", "content": "回头准备写个总结",
                    "ts": BASE - 2 * DAY} for _ in range(1))
        assert pick_recall(records, now=BASE) is not None

    def should_return_none_when_lookback_disabled(self):
        records = [_rec("user", "明天打算写点东西", BASE - 2 * DAY)]
        assert pick_recall(records, now=BASE, lookback_days=0) is None


# ---------- 久别重逢 ----------

class TestReunion:
    def should_stay_silent_for_short_gaps(self):
        assert reunion_greeting(5.0) is None
        assert reunion_hint(5.0) == ""

    def should_use_stronger_wording_for_longer_absence(self):
        one_day = reunion_greeting(24.0)
        many_days = reunion_greeting(96.0)
        assert "好久不见" in one_day
        assert "好几天没见" in many_days
        assert "想你" in reunion_greeting(48.0)
        assert "天" in reunion_hint(96.0)

    def should_honor_custom_threshold(self):
        assert reunion_greeting(10.0, min_hours=20.0) is None
        assert reunion_greeting(10.0, min_hours=8.0) is not None


# ---------- 情境短语与 hint ----------

class TestContext:
    def should_label_monday_morning_and_late_night(self):
        assert context_phrase(hour=8, weekday=0) == "周一早晨"
        assert context_phrase(hour=23, weekday=2) == "深夜"
        assert context_phrase(hour=3, weekday=2) == "深夜"
        assert context_phrase(hour=8, weekday=2) == "清晨"
        assert context_phrase(hour=20, weekday=5) == "晚上"
        assert context_phrase(hour=13, weekday=2) == "午间"
        assert context_phrase(hour=15, weekday=2) == ""

    def should_prioritize_user_situation_over_clock(self):
        assert context_phrase(hour=8, weekday=0, focusing=True) == "你正忙着，说话轻一点"
        assert context_phrase(hour=8, weekday=0, quiet=True) == "你正忙着，说话轻一点"
        assert context_phrase(hour=8, weekday=0,
                              just_finished_focus=True) == "刚结束一场专注"

    def should_energize_monday_morning_but_keep_legacy_default(self):
        assert context_hint(BehaviorContext(hour=8, weekday=0), False) is CONTEXT_ENERGIZE
        assert context_hint(BehaviorContext(hour=8), False) is CONTEXT_STRETCH
        assert context_hint(BehaviorContext(hour=14), False) is None

    def should_stretch_after_focus_and_sleep_late(self):
        assert context_hint(BehaviorContext(hour=14, just_finished_focus=True),
                            False) is CONTEXT_STRETCH
        assert context_hint(BehaviorContext(hour=23), False) is CONTEXT_SLEEP
        assert context_hint(BehaviorContext(hour=14, quiet=True), False) is CONTEXT_REST

    def should_budget_new_push_titles(self):
        assert {"我还记得", "好久不见", "我在呢"} <= set(PUSH_TITLES)


# ---------- 存在感仓储 ----------

def test_presence_store_roundtrip_and_corruption(tmp_path):
    store = PresenceStore(tmp_path)
    assert store.load()["last_seen_ts"] == 0.0
    store.touch_seen(123.5)
    store.mark_recall("2026-11-11")
    loaded = store.load()
    assert loaded["last_seen_ts"] == 123.5
    assert loaded["last_recall_day"] == "2026-11-11"

    (tmp_path / "presence.json").write_text("{broken", encoding="utf-8")
    assert store.load()["last_seen_ts"] == 0.0      # 损坏回落默认，不炸


# ---------- CompanionService ----------

class TestCompanionService:
    def should_compute_gap_and_write_current_seen(self):
        svc, clock, _bus, store, _cfg = _service(now=BASE,
                                                 last_seen_ts=BASE - 30 * 3600)
        assert svc.boot() == 30.0
        assert store.load()["last_seen_ts"] == BASE

    def should_not_fake_reunion_on_first_install(self):
        svc, clock, _bus, store, _cfg = _service(now=BASE, last_seen_ts=0.0)
        assert svc.boot() == 0.0
        assert svc.reunion_text() is None

    def should_offer_reunion_text_when_gap_long_enough(self):
        svc, _clock, _bus, _store, _cfg = _service(now=BASE,
                                                   last_seen_ts=BASE - 80 * 3600)
        svc.boot()
        assert "好几天没见" in svc.reunion_text()

    def should_disable_reunion_when_switched_off(self):
        cfg = replace(AppConfig(), companion=replace(AppConfig().companion,
                                                     reunion_enabled=False))
        svc, _clock, _bus, _store, _cfg = _service(now=BASE,
                                                   last_seen_ts=BASE - 80 * 3600,
                                                   cfg=cfg)
        svc.boot()
        assert svc.reunion_text() is None

    def should_publish_recall_once_after_delay(self):
        records = [_rec("user", "明天打算把周报写完", BASE - 3 * DAY)]
        svc, clock, collected, store, _cfg = _service(records, now=BASE)
        svc.boot()
        svc.tick(BASE + RECALL_DELAY_S - 1)
        assert collected == []
        svc.tick(BASE + RECALL_DELAY_S + 1)
        speaks = [e for e in collected if isinstance(e, SpeakRequestedEvent)]
        assert len(speaks) == 1 and speaks[0].title == "我还记得"
        assert "周报" in speaks[0].text
        assert store.load()["last_recall_day"] != ""
        svc.tick(BASE + RECALL_DELAY_S + 50)               # 当天不重复
        assert len([e for e in collected
                    if isinstance(e, SpeakRequestedEvent)]) == 1

    def should_not_publish_when_recall_disabled_or_no_candidate(self):
        cfg = replace(AppConfig(), companion=replace(AppConfig().companion,
                                                     recall_enabled=False))
        svc, _clock, collected, _store, _cfg = _service(
            [_rec("user", "明天打算写周报", BASE - 3 * DAY)], now=BASE, cfg=cfg)
        svc.tick(BASE + RECALL_DELAY_S + 1)
        assert collected == []

        svc2, _c2, collected2, _s2, _cfg2 = _service([], now=BASE)
        svc2.tick(BASE + RECALL_DELAY_S + 1)
        assert collected2 == []

    def should_expose_candidate_and_prompt_line(self):
        records = [_rec("user", "明天打算把周报写完", BASE - 3 * DAY)]
        svc, _clock, _bus, _store, _cfg = _service(records, now=BASE)
        assert svc.recall_candidate().days_ago == 3
        assert "周报" in svc.prompt_line()
        assert "只有在话题自然相关时" in svc.prompt_line()

    def should_skip_prompt_when_memory_prompt_disabled(self):
        cfg = replace(AppConfig(), companion=replace(
            AppConfig().companion, memory_prompt_enabled=False))
        svc, _clock, _bus, _store, _cfg = _service(
            [_rec("user", "明天打算写周报", BASE - 3 * DAY)], now=BASE, cfg=cfg)
        assert svc.prompt_line() == ""

    def should_skip_when_already_recalled_today(self):
        svc, _clock, collected, _store, _cfg = _service(
            [_rec("user", "明天打算写周报", BASE - 3 * DAY)], now=BASE,
            last_recall_day=day_key(BASE + RECALL_DELAY_S + 1))
        svc.tick(BASE + RECALL_DELAY_S + 1)
        assert collected == []
        assert svc._recall_fired is True


# ---------- FocusService：刚结束专注 ----------

class TestFocusJustFinished:
    def _service(self):
        clock = FakeClock()
        bus, collected = make_collecting_bus()
        return FocusService(clock, bus, lambda _entry: None), clock, collected

    def should_mark_finish_and_expire(self):
        svc, clock, _bus = self._service()
        assert not svc.just_finished()
        svc.start(1.0)
        clock.advance(61)
        svc.tick()
        assert svc.just_finished()
        clock.advance(301)
        assert not svc.just_finished()

    def should_not_mark_cancelled_session(self):
        svc, clock, _bus = self._service()
        svc.start(25.0)
        clock.advance(60)
        svc.cancel()
        assert not svc.just_finished()


# ---------- 可干预的共处：用户能改变它当下在做什么 ----------

class TestInteract:
    def _ctx(self):
        from test_pet_controller import make_ctx
        return make_ctx(idle_s=0.0)

    def should_stretch_back_when_petted_while_walking(self):
        from WisePet.domain.state_machine import BehaviorState
        from WisePet.domain.events import BehaviorChangedEvent

        ctx = self._ctx()
        controller, pet = ctx["controller"], ctx["pet"]
        controller._transition_to(BehaviorState.WALKING, autonomous=True)
        ctx["events"].clear()

        assert controller.interact("pet") is True
        assert pet.state is BehaviorState.STRETCHING
        assert any(isinstance(e, BehaviorChangedEvent) for e in ctx["events"])

    def should_turn_to_user_when_called_while_sleeping(self):
        from WisePet.domain.state_machine import BehaviorState
        from WisePet.domain.events import SpeakRequestedEvent

        ctx = self._ctx()
        controller, pet = ctx["controller"], ctx["pet"]
        controller._transition_to(BehaviorState.SLEEPING, autonomous=True)
        ctx["events"].clear()

        assert controller.interact("call") is True
        assert pet.state is BehaviorState.SPEAKING
        assert any(isinstance(e, SpeakRequestedEvent) for e in ctx["events"])

    def should_not_interrupt_drag_or_conversation(self):
        from WisePet.domain.state_machine import BehaviorState

        ctx = self._ctx()
        controller, pet = ctx["controller"], ctx["pet"]
        controller._transition_to(BehaviorState.THINKING, autonomous=False)
        assert controller.interact("pet") is False
        assert pet.state is BehaviorState.THINKING


# ---------- 配置 ----------

class TestCompanionConfig:
    def should_roundtrip_companion_settings(self):
        cfg = replace(AppConfig(), companion=replace(
            AppConfig().companion, enabled=False, recall_lookback_days=30,
            reunion_min_hours=8.0, interactive=False))
        back = AppConfig.from_dict(cfg.to_dict())
        assert back.companion.enabled is False
        assert back.companion.recall_lookback_days == 30
        assert back.companion.reunion_min_hours == 8.0
        assert back.companion.interactive is False

    def should_clamp_invalid_values_and_default_missing_section(self):
        raw = {"companion": {"reunion_min_hours": -5,
                             "recall_lookback_days": 9999,
                             "enabled": "not-a-bool"}}
        cfg = AppConfig.from_dict(raw)
        assert cfg.companion.reunion_min_hours == 1.0
        assert cfg.companion.recall_lookback_days == 90
        assert cfg.companion.enabled is True          # 非空字符串按 truthy 处理，不崩
        assert AppConfig().companion.contextual is True
