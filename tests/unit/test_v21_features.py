"""v2.0/v2.1 新功能测试：勿扰时段、番茄钟、每日提醒持久化与取消、协议解析。"""
from WisePet.app.action_protocol import (
    is_cancel_reminders_command,
    parse_daily_reminder_command,
    parse_focus_command,
)
from WisePet.app.focus_service import FocusLog, FocusService
from WisePet.app.one_shot_reminders import OneShotReminders
from WisePet.app.reminder_service import ReminderService
from WisePet.domain.config_model import AppConfig
from WisePet.domain.events import ReminderDueEvent, SpeakRequestedEvent
from fakes import FakeClock, make_collecting_bus


# ---------- 勿扰时段 ----------

def make_reminder(deferral=None):
    cfg = AppConfig()
    cfg.reminders.water.interval_min = 10.0
    clock = FakeClock()
    bus, collected = make_collecting_bus()
    service = ReminderService(clock, bus, lambda: cfg, deferral_provider=deferral)
    service.start()
    return service, clock, collected


class TestQuietHours:
    def should_defer_to_quiet_end(self):
        from WisePet.domain.events import KIND_WATER
        remaining = [3600.0, 0.0]                    # 先在勿扰期内，后出时段
        svc, clock, collected = make_reminder(
            deferral=lambda: remaining.pop(0) if remaining else 0.0)
        clock.advance(11 * 60)
        svc.tick()
        assert collected == []                       # 勿扰期内顺延

        clock.advance(3700)
        svc.tick()
        due = [e for e in collected
               if isinstance(e, ReminderDueEvent) and e.kind == KIND_WATER]
        assert len(due) == 1                         # 时段结束照常提醒

    def should_fire_immediately_when_not_quiet(self):
        svc, clock, collected = make_reminder(deferral=lambda: 0.0)
        clock.advance(11 * 60)
        svc.tick()
        assert len(collected) == 1

    def should_cover_overnight_window(self):
        """跨零点窗口（如 23~7）的判定交给组装根，这里验证顺延秒数被采纳。"""
        svc, clock, collected = make_reminder(deferral=lambda: 7200.0)
        clock.advance(11 * 60)
        svc.tick()
        assert collected == []


# ---------- 番茄钟 ----------

def make_focus(log_entries):
    clock = FakeClock()
    bus, collected = make_collecting_bus()
    svc = FocusService(clock, bus, append_entry=log_entries.append)
    return svc, clock, collected


class TestFocus:
    def should_notify_and_log_when_completed(self):
        entries = []
        svc, clock, collected = make_focus(entries)
        svc.start(25)
        clock.advance(26 * 60)
        svc.tick()

        speaks = [e for e in collected if isinstance(e, SpeakRequestedEvent)]
        assert len(speaks) == 1 and speaks[0].title == "专注结束"
        assert len(entries) == 1 and entries[0]["minutes"] == 25.0
        assert svc.running is False

    def should_not_fire_twice(self):
        entries = []
        svc, clock, collected = make_focus(entries)
        svc.start(25)
        clock.advance(26 * 60)
        svc.tick()
        svc.tick()
        assert len(collected) == 1 and len(entries) == 1

    def should_cancel_without_logging(self):
        entries = []
        svc, clock, _collected = make_focus(entries)
        svc.start(25)
        clock.advance(5 * 60)
        svc.cancel()

        assert svc.running is False and entries == []

    def should_summarize_today(self):
        entries = [{"date": "2026-11-06", "minutes": 25.0},
                   {"date": "2026-11-06", "minutes": 15.0},
                   {"date": "2026-11-05", "minutes": 50.0}]
        assert FocusLog.summarize(entries, "2026-11-06") == (40.0, 2)


# ---------- 每日提醒与取消 ----------

class TestDailyReminder:
    def should_schedule_daily_and_rearm_after_fire(self):
        clock = FakeClock()
        bus, collected = make_collecting_bus()
        svc = OneShotReminders(clock, bus)
        svc.schedule_at("交日报", 9, 0, daily=True)
        saved = []
        svc._save_entries = saved.append
        svc._save()

        clock.advance(10 * 3600)                 # 越过 9 点（FakeClock 基点当天）
        svc.tick()
        speaks = [e for e in collected if isinstance(e, SpeakRequestedEvent)]
        assert len(speaks) == 1 and speaks[0].text == "交日报"
        assert len(svc.pending) == 1             # 续排明天
        assert svc.pending[0][0] > clock.now()

    def should_cancel_all(self):
        clock = FakeClock()
        bus, _collected = make_collecting_bus()
        svc = OneShotReminders(clock, bus)
        svc.schedule_delay("一", 5.0)
        svc.schedule_delay("二", 6.0)

        assert svc.cancel_all() == 2
        assert svc.pending == []

    def should_restore_pending_from_store(self):
        clock = FakeClock()
        bus, _collected = make_collecting_bus()
        store = {"items": [{"due_at": clock.now() + 300, "text": "还没到点", "daily": ""}]}
        svc = OneShotReminders(clock, bus,
                               load_entries=lambda: store["items"],
                               save_entries=lambda items: store.__setitem__("items", items))

        assert svc.pending[0][1] == "还没到点"

    def should_drop_expired_one_shot_on_restore(self):
        clock = FakeClock()
        bus, collected = make_collecting_bus()
        svc = OneShotReminders(clock, bus,
                               load_entries=lambda: [
                                   {"due_at": clock.now() - 300, "text": "过期了", "daily": ""}])
        assert svc.pending == [] and collected == []


# ---------- 协议解析 ----------

class TestProtocolParsing:
    def should_parse_daily(self):
        action = parse_daily_reminder_command("每天14:30提醒我交日报")
        assert action is not None
        assert action.text == "交日报" and action.at_hm == "14:30" and action.daily

    def should_return_none_for_bad_daily(self):
        assert parse_daily_reminder_command("每天25:00提醒我") is None

    def should_detect_cancel(self):
        assert is_cancel_reminders_command("取消所有提醒")
        assert is_cancel_reminders_command("取消提醒")
        assert not is_cancel_reminders_command("别取消提醒")

    def should_parse_focus(self):
        assert parse_focus_command("专注25分钟") == 25.0
        assert parse_focus_command("来个番茄钟") == 25.0
        assert parse_focus_command("陪我专注 50 分钟") == 50.0
        assert parse_focus_command("专注力是什么") == 25.0     # 无数字 → 默认 25

    def should_not_treat_focus_as_reminder(self):
        from WisePet.app.action_protocol import parse_reminder_command
        assert parse_reminder_command("专注25分钟") is None
