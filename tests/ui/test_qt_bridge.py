"""事件桥冒烟：EventBus → Qt Signal 转投与类型分派（技术方案 §2 Qt 隔离策略）。"""
from __future__ import annotations

import threading

import pytest
import shiboken6
from PySide6.QtCore import QThread

from WisePet.app.event_bus import EventBus
from WisePet.domain.errors import AIGatewayError
from WisePet.domain.events import (
    BehaviorChangedEvent,
    ChatErrorEvent,
    ChatReplyChunkEvent,
    SpeakRequestedEvent,
)
from WisePet.domain.state_machine import BehaviorState
from WisePet.ui.qt_bridge import EventRouter, QtEventBridge


class _SubSpeak(SpeakRequestedEvent):
    """用于验证分派表按 isinstance 匹配（含子类）。"""


class _PublishThread(QThread):
    """模拟 AiWorker：在非主线程发布领域事件。"""

    def __init__(self, bus, event):
        super().__init__()
        self._bus = bus
        self._event = event

    def run(self) -> None:
        self._bus.publish(self._event)


@pytest.fixture
def bus():
    return EventBus()


def _behavior() -> BehaviorChangedEvent:
    return BehaviorChangedEvent(pet_id="mochi", old=BehaviorState.IDLE,
                               new=BehaviorState.THINKING)


def should_forward_bus_event_to_qt_signal_when_published(bus, qtbot):
    bridge = QtEventBridge(bus)
    event = _behavior()

    with qtbot.waitSignal(bridge.event, raising=True, timeout=1000) as blocker:
        bus.publish(event)

    assert blocker.args == [event]


def should_forward_every_event_type_when_subscribed_by_bridge(bus, qtbot):
    bridge = QtEventBridge(bus)
    received = []
    bridge.event.connect(received.append)

    bus.publish(SpeakRequestedEvent(text="你好"))
    bus.publish(ChatErrorEvent(kind=AIGatewayError.NETWORK))

    assert len(received) == 2


def should_deliver_event_on_main_thread_when_published_from_worker(bus, qtbot):
    """跨线程转投的关键保证：处理器始终在主线程跑，ui 可安全碰控件。"""
    bridge = QtEventBridge(bus)
    main_thread = threading.get_ident()
    threads = []
    bridge.event.connect(lambda _e: threads.append(threading.get_ident()))

    worker = _PublishThread(bus, SpeakRequestedEvent(text="来自子线程"))
    worker.start()
    qtbot.waitUntil(lambda: bool(threads), timeout=3000)
    worker.wait(3000)

    assert threads == [main_thread]


def should_call_only_matching_handler_when_event_routed(bus, qtbot):
    bridge = QtEventBridge(bus)
    router = EventRouter(bridge)
    speak_calls: list = []
    chunk_calls: list = []
    router.on(SpeakRequestedEvent, speak_calls.append)
    router.on(ChatReplyChunkEvent, chunk_calls.append)

    bus.publish(SpeakRequestedEvent(text="播报"))
    bus.publish(ChatReplyChunkEvent(text_delta="字"))

    assert [event.text for event in speak_calls] == ["播报"]
    assert [event.text_delta for event in chunk_calls] == ["字"]


def should_match_subclass_when_routing_by_type(bus, qtbot):
    bridge = QtEventBridge(bus)
    router = EventRouter(bridge)
    matched = []
    router.on(SpeakRequestedEvent, matched.append)

    bus.publish(_SubSpeak(text="子类事件"))

    assert len(matched) == 1


def should_keep_other_handlers_running_when_one_raises(bus, qtbot):
    bridge = QtEventBridge(bus)
    router = EventRouter(bridge)
    survived = []

    def boom(_event):
        raise RuntimeError("handler broken")

    router.on(SpeakRequestedEvent, boom)
    router.on(SpeakRequestedEvent, survived.append)

    bus.publish(SpeakRequestedEvent(text="仍要播报"))

    assert len(survived) == 1


def should_drop_event_without_raising_when_bridge_destroyed(bus, qtbot):
    """退出竞态下 C++ 对象已销毁：丢弃事件而非崩溃（PRD 可靠性）。"""
    bridge = QtEventBridge(bus)
    shiboken6.delete(bridge)

    bridge._on_event(_behavior())

    assert not shiboken6.isValid(bridge)
