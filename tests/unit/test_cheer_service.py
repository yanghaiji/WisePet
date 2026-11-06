"""CheerService 测试：定时鼓励推送、AI 生成、本地语录兜底、间隔热生效。"""
import time

from WisePet.app.cheer_service import CheerService, LOCAL_CHEER_LINES
from WisePet.domain.config_model import AppConfig, CheerSettings
from WisePet.domain.events import SpeakRequestedEvent
from fakes import FakeClock, make_collecting_bus


def make_svc(generator=None, *, cheer: CheerSettings | None = None):
    cfg = AppConfig()
    if cheer is not None:
        cfg.cheer = cheer
    clock = FakeClock()
    bus, collected = make_collecting_bus()
    return CheerService(clock, bus, lambda: cfg, generator=generator), clock, collected


def _wait(collected, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not collected:
        time.sleep(0.01)


class TestCheerService:
    def should_publish_local_line_when_no_generator(self):
        svc, clock, collected = make_svc(generator=None)
        svc.start()
        clock.advance(181)
        svc.tick()
        _wait(collected)

        speaks = [e for e in collected if isinstance(e, SpeakRequestedEvent)]
        assert len(speaks) == 1
        assert speaks[0].text in LOCAL_CHEER_LINES
        assert speaks[0].source == "cheer"
        assert speaks[0].title == "加油打气"

    def should_publish_ai_line_when_generator_hits(self):
        svc, clock, collected = make_svc(generator=lambda: "长风破浪会有时！")
        svc.start()
        clock.advance(181)
        svc.tick()
        _wait(collected)

        speaks = [e for e in collected if isinstance(e, SpeakRequestedEvent)]
        assert speaks and speaks[0].text == "长风破浪会有时！"

    def should_fall_back_to_local_when_generator_fails(self):
        def boom():
            raise RuntimeError("ai down")
        svc, clock, collected = make_svc(generator=boom)
        svc.start()
        clock.advance(181)
        svc.tick()
        _wait(collected)

        speaks = [e for e in collected if isinstance(e, SpeakRequestedEvent)]
        assert speaks and speaks[0].text in LOCAL_CHEER_LINES

    def should_skip_when_disabled(self):
        svc, clock, collected = make_svc(
            generator=lambda: "x", cheer=CheerSettings(enabled=False))
        svc.start()
        clock.advance(181)
        svc.tick()
        _wait(collected)

        assert collected == []

    def should_reschedule_when_interval_changes(self):
        svc, clock, collected = make_svc(generator=lambda: "加油")
        svc.start()
        svc.reschedule()                       # 改配置后立即按新间隔重排
        clock.advance(61)                      # 默认 3min 未到……
        assert collected == []
        # 间隔改成 1 分钟后 reschedule，61s 应触发
        cfg_cheer = CheerSettings(interval_min=1.0)
        svc2, clock2, collected2 = make_svc(lambda: "加油", cheer=cfg_cheer)
        svc2.start()
        svc2.reschedule()
        clock2.advance(61)
        svc2.tick()
        _wait(collected2)
        assert len(collected2) == 1
