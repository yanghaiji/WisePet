"""会话状态/记录事件：ChatService 把「一轮对话」发布成可见、可落盘的事实（FR v1.8 C13）。"""
from __future__ import annotations

from WisePet.app.chat_service import ChatService
from WisePet.domain.chat_record import day_key
from WisePet.domain.chat_session import ChatSession
from WisePet.domain.errors import AIGatewayError
from WisePet.domain.events import (
    PHASE_DONE,
    PHASE_ERROR,
    PHASE_REPLYING,
    PHASE_THINKING,
    ChatStateChangedEvent,
    ChatTurnRecordedEvent,
)
from WisePet.domain.ports import AIConfig
from fakes import FakeGateway, FakeResponder, make_collecting_bus


def _service(gateway, *, api_key="sk-x", local=None):
    bus, collected = make_collecting_bus()
    service = ChatService(gateway, ChatSession(max_turns=20), bus,
                          lambda: AIConfig(api_key=api_key),
                          lambda: "你是桌宠", local_responder=local)
    return service, collected


def _phases(collected):
    return [e.phase for e in collected if isinstance(e, ChatStateChangedEvent)]


def _turns(collected):
    return [(e.role, e.content, e.source) for e in collected
            if isinstance(e, ChatTurnRecordedEvent)]


def should_publish_full_turn_and_phases_when_ai_reply_finishes():
    service, collected = _service(FakeGateway(chunks=["你", "好"]))

    service.send("在吗")
    service.process_next()

    assert _phases(collected) == [PHASE_THINKING, PHASE_THINKING,
                                  PHASE_REPLYING, PHASE_DONE]
    assert _turns(collected) == [("user", "在吗", "user"),
                                 ("assistant", "你好", "ai")]
    assert all(e.source == "ai" for e in collected
               if isinstance(e, ChatStateChangedEvent))


def should_label_local_brain_and_record_local_turn_when_no_key():
    service, collected = _service(FakeGateway(), api_key="",
                                  local=FakeResponder("喵～我在"))

    service.send("你好")
    service.process_next()

    assert _turns(collected) == [("user", "你好", "user"),
                                 ("assistant", "喵～我在", "local")]
    assert _phases(collected)[-1] == PHASE_DONE
    assert all(e.source == "local" for e in collected
               if isinstance(e, ChatStateChangedEvent))


def should_publish_error_phase_and_fallback_turn_when_gateway_fails():
    gateway = FakeGateway(error=AIGatewayError(AIGatewayError.NETWORK, "offline"))
    service, collected = _service(gateway)

    service.send("在吗")
    service.process_next()

    errors = [e for e in collected if isinstance(e, ChatStateChangedEvent)
              and e.phase == PHASE_ERROR]
    assert len(errors) == 1 and errors[0].detail == AIGatewayError.NETWORK
    assert ("assistant", service.fallback_text(AIGatewayError.NETWORK),
            "fallback") in _turns(collected)


def should_stamp_today_session_id_on_every_turn():
    service, collected = _service(FakeGateway(chunks=["好"]))

    service.send("你好")
    service.process_next()

    assert all(e.session_id == day_key() for e in collected
               if isinstance(e, ChatTurnRecordedEvent))


def should_not_publish_turn_when_blank_message_rejected():
    service, collected = _service(FakeGateway())

    assert service.send("   ") is False
    assert _turns(collected) == []
    assert _phases(collected) == []


def should_not_record_assistant_turn_when_stream_empty():
    service, collected = _service(FakeGateway(chunks=[]))
    service.send("你好")
    service.process_next()

    assert _turns(collected) == [("user", "你好", "user")]
    assert _phases(collected)[-1] == PHASE_DONE
