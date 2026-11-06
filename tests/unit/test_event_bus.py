"""EventBus 单元测试：定向/通配订阅、监听器异常隔离。"""
from WisePet.app.event_bus import EventBus
from WisePet.domain.events import BehaviorChangedEvent, SpeakRequestedEvent
from WisePet.domain.state_machine import BehaviorState


class TestEventBus:
    def should_deliver_to_type_subscriber_when_published(self):
        bus = EventBus()
        got = []
        bus.subscribe(SpeakRequestedEvent, got.append)
        event = SpeakRequestedEvent("hi")
        bus.publish(event)
        assert got == [event]

    def should_skip_other_type_subscribers_when_published(self):
        bus = EventBus()
        got = []
        bus.subscribe(SpeakRequestedEvent, got.append)
        bus.publish(BehaviorChangedEvent("p", BehaviorState.IDLE, BehaviorState.WALKING))
        assert got == []

    def should_receive_all_types_when_subscribed_wildcard(self):
        bus = EventBus()
        got = []
        bus.subscribe(None, got.append)
        e1 = SpeakRequestedEvent("a")
        e2 = BehaviorChangedEvent("p", BehaviorState.IDLE, BehaviorState.HURT)
        bus.publish(e1)
        bus.publish(e2)
        assert got == [e1, e2]

    def should_keep_other_handlers_when_one_raises(self):
        bus = EventBus()
        got = []

        def boom(_event):
            raise RuntimeError("handler broken")

        bus.subscribe(SpeakRequestedEvent, boom)
        bus.subscribe(SpeakRequestedEvent, got.append)
        bus.publish(SpeakRequestedEvent("ok"))
        assert got and got[0].text == "ok"
