"""OneShotReminders 测试：排期、到点播报、绝对时刻、上限保护。"""
import time

import pytest

from WisePet.app.action_protocol import RemindAction
from WisePet.app.one_shot_reminders import MAX_PENDING, OneShotReminders
from WisePet.domain.events import SpeakRequestedEvent
from fakes import FakeClock, make_collecting_bus


def make_svc(polisher=None):
    clock = FakeClock()
    bus, collected = make_collecting_bus()
    return OneShotReminders(clock, bus, polisher=polisher), clock, collected


def _wait_collected(collected, timeout=2.0):
    import time as _time
    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline and not collected:
        _time.sleep(0.01)


class TestScheduleAndFire:
    def should_fire_speak_when_due(self):
        svc, clock, collected = make_svc()
        svc.schedule(RemindAction(text="开会", delay_min=5.0))

        clock.advance(4.9 * 60)
        svc.tick()
        assert collected == []                 # 未到点不播报

        clock.advance(0.2 * 60)
        svc.tick()
        speaks = [e for e in collected if isinstance(e, SpeakRequestedEvent)]
        assert len(speaks) == 1 and speaks[0].text == "开会"
        assert speaks[0].source == "reminder"

    def should_not_fire_twice(self):
        svc, clock, collected = make_svc()
        svc.schedule(RemindAction(text="喝水", delay_min=1.0))
        clock.advance(2 * 60)
        svc.tick()
        svc.tick()
        assert len([e for e in collected if isinstance(e, SpeakRequestedEvent)]) == 1

    def should_schedule_at_future_time_today(self):
        svc, clock, _collected = make_svc()
        base = time.localtime(clock.now())
        # 下一分钟（可能与当前同小时），必然在 1 小时内到点
        minute = (base.tm_min + 1) % 60
        due_at = svc.schedule_at("例会", base.tm_hour, minute)
        assert clock.now() < due_at <= clock.now() + 3600.0

    def should_roll_to_tomorrow_when_time_passed(self):
        svc, clock, _collected = make_svc()
        base = time.localtime(clock.now())
        # 排「当前 hh:mm:00」：now 带秒数一定 >= 它 → 顺延到明天
        due_at = svc.schedule_at("交日报", base.tm_hour, base.tm_min)
        assert due_at >= clock.now() + 86400.0 - 60.0

    def should_reject_blank_text_and_bad_time(self):
        svc, _clock, _collected = make_svc()
        with pytest.raises(ValueError):
            svc.schedule_delay("  ", 5.0)
        with pytest.raises(ValueError):
            svc.schedule_at("开会", 25, 0)

    def should_cap_pending_and_keep_soonest(self):
        svc, _clock, _collected = make_svc()
        for index in range(MAX_PENDING + 5):
            svc.schedule_delay(f"任务{index}", delay_min=index + 1.0)
        assert len(svc.pending) == MAX_PENDING
        assert svc.pending[0][1] == "任务0"     # 最早到点的保留


class TestPolish:
    """到点话术 AI 润色：成功用润色文本，失败/异常回落原文（提醒必响）。"""

    def should_publish_polished_text_when_polisher_hits(self):
        svc, clock, collected = make_svc(polisher=lambda text: f"喵~ {text}！")
        svc.schedule_delay("开会", delay_min=1.0)
        clock.advance(2 * 60)
        svc.tick()
        _wait_collected(collected)

        speaks = [e for e in collected if isinstance(e, SpeakRequestedEvent)]
        assert len(speaks) == 1 and speaks[0].text == "喵~ 开会！"
        assert speaks[0].source == "reminder"

    def should_fall_back_to_original_when_polisher_returns_none(self):
        svc, clock, collected = make_svc(polisher=lambda text: None)
        svc.schedule_delay("喝水", delay_min=1.0)
        clock.advance(2 * 60)
        svc.tick()
        _wait_collected(collected)

        speaks = [e for e in collected if isinstance(e, SpeakRequestedEvent)]
        assert len(speaks) == 1 and speaks[0].text == "喝水"

    def should_fall_back_when_polisher_raises(self):
        def boom(text):
            raise RuntimeError("network down")
        svc, clock, collected = make_svc(polisher=boom)
        svc.schedule_delay("交报表", delay_min=1.0)
        clock.advance(2 * 60)
        svc.tick()
        _wait_collected(collected)

        speaks = [e for e in collected if isinstance(e, SpeakRequestedEvent)]
        assert len(speaks) == 1 and speaks[0].text == "交报表"

    def should_publish_immediately_without_polisher(self):
        svc, clock, collected = make_svc()          # 未注入润色器：同步原路径
        svc.schedule_delay("开会", delay_min=1.0)
        clock.advance(2 * 60)
        svc.tick()

        speaks = [e for e in collected if isinstance(e, SpeakRequestedEvent)]
        assert len(speaks) == 1 and speaks[0].text == "开会"
