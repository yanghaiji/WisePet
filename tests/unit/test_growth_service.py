"""GrowthService 测试：互动累加、跨日陪伴、升级播报、禁用短路、落盘持久。"""
from WisePet.app.growth_service import GrowthService
from WisePet.domain.config_model import AppConfig, GrowthSettings
from WisePet.domain.events import SpeakRequestedEvent
from WisePet.domain.growth import (
    CHAT_POINTS,
    COMPANION_DAY_POINTS,
    FEED_POINTS,
    LEVEL_BASE_COST,
    PET_POINTS,
)
from WisePet.infra.growth_store import GrowthData
from fakes import FakeClock, make_collecting_bus


class MemStore:
    def __init__(self, data: GrowthData | None = None):
        self._data = data or GrowthData()
        self.saves = 0

    def load(self) -> GrowthData:
        return self._data

    def save(self, data: GrowthData) -> None:
        self._data = data
        self.saves += 1


def make_svc(*, enabled=True, day="2026-11-06", store=None):
    cfg = AppConfig(growth=GrowthSettings(enabled=enabled))
    clock = FakeClock()
    bus, collected = make_collecting_bus()
    store = store or MemStore()
    holder = {"day": day}
    svc = GrowthService(clock, bus, store, config_provider=lambda: cfg,
                        day_key=lambda _ts: holder["day"])
    return svc, clock, collected, store, holder


class TestAccumulate:
    def should_count_first_day_on_boot(self):
        svc, _c, collected, store, _h = make_svc()
        # 开机即计入第一个陪伴日，但不播报
        assert svc.current().points == COMPANION_DAY_POINTS
        assert svc.current().companion_days == 1
        assert collected == []
        assert store.saves >= 1

    def should_add_points_per_interaction(self):
        svc, _c, _col, _s, _h = make_svc()
        base = svc.current().points
        svc.on_feed()
        assert svc.current().points == base + FEED_POINTS
        svc.on_pet()
        assert svc.current().points == base + FEED_POINTS + PET_POINTS
        svc.on_chat()
        assert svc.current().points == base + FEED_POINTS + PET_POINTS + CHAT_POINTS

    def should_announce_once_when_crossing_level(self):
        svc, _c, collected, _s, _h = make_svc()
        # 从陪伴日底分出发，喂够到升级门槛
        target = LEVEL_BASE_COST
        for _ in range(10):
            svc.on_feed()
        speaks = [e for e in collected if isinstance(e, SpeakRequestedEvent)]
        assert svc.current().points >= target
        assert svc.current().level >= 2
        assert speaks and "级" in speaks[0].text


class TestCompanionDay:
    def should_add_new_day_on_tick_after_midnight(self):
        svc, clock, _col, _s, holder = make_svc()
        assert svc.current().companion_days == 1
        holder["day"] = "2026-11-07"
        clock.advance(3600)
        before = svc.current().points
        svc.tick()
        assert svc.current().companion_days == 2
        assert svc.current().points == before + COMPANION_DAY_POINTS

    def should_not_double_count_same_day(self):
        svc, clock, _col, _s, _h = make_svc()
        before = svc.current().points
        clock.advance(60)
        svc.tick()
        assert svc.current().points == before
        assert svc.current().companion_days == 1

    def should_not_add_new_day_when_disabled(self):
        svc, clock, _col, _s, holder = make_svc(enabled=False)
        assert svc.current().points == 0 and svc.current().companion_days == 0
        holder["day"] = "2026-11-07"
        clock.advance(3600)
        svc.tick()
        assert svc.current().points == 0


class TestDisabled:
    def should_ignore_interactions(self):
        svc, _c, collected, store, _h = make_svc(enabled=False)
        svc.on_feed()
        svc.on_pet()
        svc.on_chat()
        assert svc.current().points == 0
        assert collected == []
        assert store.saves == 0
