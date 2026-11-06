"""ReminderService 测试：计时触发、到期必响（全屏也不例外）、合并播报、间隔下限。"""
import pytest

from WisePet.app.reminder_service import ReminderService
from WisePet.domain.config_model import AppConfig
from WisePet.domain.events import KIND_SEDENTARY, KIND_WATER, ReminderDueEvent
from fakes import FakeClock, make_collecting_bus


@pytest.fixture()
def ctx():
    cfg = AppConfig()
    cfg.reminders.water.interval_min = 10.0
    cfg.reminders.sedentary.interval_min = 10.0
    clock = FakeClock()
    bus, collected = make_collecting_bus()
    service = ReminderService(clock, bus, lambda: cfg)
    service.start()
    return {"cfg": cfg, "clock": clock,
            "events": collected, "service": service}


def due_events(events):
    return [e for e in events if isinstance(e, ReminderDueEvent)]


class TestTiming:
    def should_not_fire_when_interval_not_reached(self, ctx):
        ctx["clock"].advance(9 * 60)
        ctx["service"].tick()
        assert due_events(ctx["events"]) == []

    def should_fire_water_when_interval_reached(self, ctx):
        ctx["clock"].advance(11 * 60)
        ctx["service"].tick()
        water = [e for e in due_events(ctx["events"]) if e.kind == KIND_WATER]
        assert len(water) == 1
        assert "喝水" in water[0].text

    def should_reschedule_after_fire(self, ctx):
        ctx["clock"].advance(11 * 60)
        ctx["service"].tick()
        ctx["events"].clear()                 # 只看第二次 tick 的新事件
        ctx["clock"].advance(5 * 60)  # 距上次触发仅 5min，未到新周期
        ctx["service"].tick()
        assert due_events(ctx["events"]) == []

    def should_not_fire_when_disabled(self, ctx):
        ctx["service"].set_enabled(KIND_WATER, enabled=False)
        ctx["service"].set_enabled(KIND_SEDENTARY, enabled=False)
        ctx["clock"].advance(60 * 60)
        ctx["service"].tick()
        assert due_events(ctx["events"]) == []

    def should_arm_timer_on_first_tick_when_start_skipped(self, ctx):
        """漏调 start() 也要自恢复：首个 tick 只排期、不立刻播报。"""
        bus, collected = make_collecting_bus()
        service = ReminderService(ctx["clock"], bus, lambda: ctx["cfg"])

        service.tick()
        assert collected == []

        ctx["clock"].advance(11 * 60)
        service.tick()
        assert len(due_events(collected)) == 2   # 水和久坐各一条


class TestDndSnooze:
    """用户反馈：全屏（游戏/视频/最大化写代码）时也要提醒——到期必响，无勿扰顺延。"""

    def should_fire_even_when_user_fullscreen(self, ctx):
        # 勿扰顺延已整体移除：全屏与否不再影响提醒（真机踩过：喝水提醒被顺延吞掉）
        ctx["clock"].advance(11 * 60)
        ctx["service"].tick()
        assert len(due_events(ctx["events"])) == 2


class TestMerge:
    def should_fire_two_distinct_events_when_both_due(self, ctx):
        """同时到期各发各的：多卡按标题区分，不再合并（合并会让标题张冠李戴）。"""
        ctx["clock"].advance(11 * 60)
        ctx["service"].tick()
        fired = due_events(ctx["events"])
        kinds = sorted(e.kind for e in fired)
        assert kinds == [KIND_SEDENTARY, KIND_WATER]
        assert all("喝水" in e.text for e in fired if e.kind == KIND_WATER)
        assert all("活动" in e.text for e in fired if e.kind == KIND_SEDENTARY)


class TestSettings:
    def should_reject_when_interval_below_minimum(self, ctx):
        with pytest.raises(ValueError):
            ctx["service"].set_interval(KIND_WATER, minutes=0.9)

    def should_use_new_interval_after_set(self, ctx):
        ctx["service"].set_interval(KIND_WATER, minutes=20.0)
        ctx["clock"].advance(15 * 60)
        ctx["service"].tick()
        water = [e for e in due_events(ctx["events"]) if KIND_WATER == e.kind or "喝水" in e.text]
        assert water == []  # 20min 周期未到，15min 不触发

    def should_restart_timing_when_reenabled(self, ctx):
        ctx["service"].set_enabled(KIND_WATER, enabled=False)
        ctx["service"].set_enabled(KIND_WATER, enabled=True)
        ctx["clock"].advance(11 * 60)
        ctx["service"].tick()
        assert any("喝水" in e.text for e in due_events(ctx["events"]))

    def should_not_schedule_when_kind_disabled(self, ctx):
        ctx["service"].set_enabled(KIND_WATER, enabled=False)

        ctx["service"].set_interval(KIND_WATER, minutes=20.0)

        # 直接断排期表：已关闭的提醒不得留下到点时间（否则保存设置后可能复活）
        assert KIND_WATER not in ctx["service"]._due_at
        ctx["clock"].advance(60 * 60)
        ctx["service"].tick()
        assert all("喝水" not in e.text for e in due_events(ctx["events"]))

    def should_reject_unknown_kind_when_setting(self, ctx):
        with pytest.raises(ValueError):
            ctx["service"].set_enabled("sleepy", True)
