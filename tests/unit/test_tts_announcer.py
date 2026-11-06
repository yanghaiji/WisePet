"""TtsAnnouncer 测试：按配置门控、带音量语速朗读、空文本短路、失败不冒泡。"""
import time

from WisePet.app.tts_announcer import TtsAnnouncer
from WisePet.domain.config_model import AppConfig, TtsSettings


class SpySynth:
    def __init__(self, raise_exc=False):
        self.calls = []
        self._raise = raise_exc

    def speak(self, text, *, volume, rate):
        if self._raise:
            raise RuntimeError("engine down")
        self.calls.append((text, volume, rate))


def _wait(calls, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not calls:
        time.sleep(0.01)


def should_not_speak_when_disabled():
    spy = SpySynth()
    TtsAnnouncer(spy, lambda: AppConfig(tts=TtsSettings(enabled=False))).announce("你好")
    time.sleep(0.05)
    assert spy.calls == []


def should_speak_with_volume_and_rate_when_enabled():
    spy = SpySynth()
    cfg = AppConfig(tts=TtsSettings(enabled=True, volume=40, rate=2))
    TtsAnnouncer(spy, lambda: cfg).announce("你好")
    _wait(spy.calls)
    assert spy.calls == [("你好", 40, 2)]


def should_skip_blank_text():
    spy = SpySynth()
    TtsAnnouncer(spy, lambda: AppConfig(tts=TtsSettings(enabled=True))).announce("   ")
    time.sleep(0.05)
    assert spy.calls == []


def should_swallow_synth_failure():
    cfg = AppConfig(tts=TtsSettings(enabled=True))
    TtsAnnouncer(SpySynth(raise_exc=True), lambda: cfg).announce("喵")
    time.sleep(0.05)   # 后台线程吞掉异常即通过


def should_skip_when_only_emoji():
    spy = SpySynth()
    TtsAnnouncer(spy, lambda: AppConfig(tts=TtsSettings(enabled=True))).announce("😀🎉✨")
    time.sleep(0.05)
    assert spy.calls == []


def should_strip_emoji_but_keep_text_when_mixed():
    spy = SpySynth()
    cfg = AppConfig(tts=TtsSettings(enabled=True))
    TtsAnnouncer(spy, lambda: cfg).announce("该喝水啦 💧😀 朋友")
    _wait(spy.calls)
    assert spy.calls[0][0] == "该喝水啦 朋友"
