"""ChatService 编排测试：入队校验、流式分发、错误映射、取消、本地模式、来源日志（PRD US-3 / v1.6 C8）。"""
import logging

import pytest
from WisePet.app.chat_service import (
    REPLY_PREVIEW_CHARS,
    ChatService,
    FALLBACK_TEXTS,
    stream_pieces,
)
from WisePet.domain.chat_session import ChatSession
from WisePet.domain.errors import AIGatewayError
from WisePet.domain.events import ChatErrorEvent, ChatReplyChunkEvent
from WisePet.domain.ports import AIConfig
from fakes import FakeGateway, FakeResponder, make_collecting_bus


def make_service(gateway, session=None, prompt="SYSTEM_PROMPT", config=None, local=None):
    bus, collected = make_collecting_bus()
    session = session or ChatSession(max_turns=20)
    service = ChatService(gateway, session, bus,
                          lambda: config or AIConfig(api_key="sk-x"),
                          lambda: prompt, local_responder=local)
    return service, bus, collected, session


class TestSendValidation:
    def should_reject_when_text_blank(self):
        service, _, _, _ = make_service(FakeGateway())
        assert service.send("   ") is False
        assert service.has_pending() is False

    def should_enqueue_when_text_valid(self):
        service, _, _, session = make_service(FakeGateway())
        assert service.send("你好") is True
        assert service.has_pending() is True
        assert session.turns[-1].content == "你好"


class TestProcessNext:
    def should_publish_deltas_and_done_when_stream_ok(self):
        gateway = FakeGateway(chunks=["你", "好", "呀"])
        service, _, collected, session = make_service(gateway)
        service.send("hi")
        assert service.process_next() is True
        deltas = [e.text_delta for e in collected if isinstance(e, ChatReplyChunkEvent)]
        assert deltas == ["你", "好", "呀", ""]
        done = [e for e in collected if isinstance(e, ChatReplyChunkEvent) and e.done]
        assert len(done) == 1 and done[0].reply == "你好呀"
        assert session.last_role == "assistant"

    def should_build_messages_with_system_prompt_when_processing(self):
        gateway = FakeGateway(chunks=["ok"])
        service, _, _, _ = make_service(gateway, prompt="你是小墨")
        service.send("今天怎么样")
        service.process_next()
        messages = gateway.calls[0][0]
        assert messages[0] == {"role": "system", "content": "你是小墨"}
        assert messages[-1] == {"role": "user", "content": "今天怎么样"}

    def should_return_false_when_queue_empty(self):
        service, _, _, _ = make_service(FakeGateway())
        assert service.process_next() is False

    def should_process_serially_when_multiple_queued(self):
        gateway = FakeGateway(chunks=["x"])
        service, _, _, _ = make_service(gateway)
        service.send("a")
        service.send("b")
        assert service.process_next() is True
        assert service.process_next() is True
        assert service.process_next() is False
        assert len(gateway.calls) == 2

    def should_not_record_assistant_turn_when_reply_empty(self):
        service, _, _, session = make_service(FakeGateway(chunks=[]))
        service.send("hi")
        service.process_next()
        assert session.last_role == "user"


class TestStreamPieces:
    def should_split_at_punctuation_when_text_has_breaks(self):
        assert stream_pieces("你好呀，我是一只猫。") == ["你好呀，", "我是一只猫。"]

    def should_cap_length_when_text_has_no_punctuation(self):
        pieces = stream_pieces("一二三四五六七八九十")

        assert pieces == ["一二三四五六七八", "九十"]

    def should_return_empty_when_text_blank(self):
        assert stream_pieces("") == []

    def should_rejoin_to_original_when_any_text(self):
        text = "喵呜～今天心情不错，要不要一起玩？"

        assert "".join(stream_pieces(text)) == text


class TestLocalMode:
    """未配 AI Key 时不得只会说“连不上”，而要能本地对话。"""

    def should_reply_locally_when_ai_key_missing(self):
        gateway = FakeGateway(chunks=["不该被调用"])
        local = FakeResponder("喵～你好呀")
        service, _, collected, session = make_service(
            gateway, config=AIConfig(api_key=""), local=local)

        service.send("你好")
        assert service.process_next() is True

        assert gateway.calls == []            # 没配 Key 不敲网关
        assert local.texts == ["你好"]
        assert not any(isinstance(e, ChatErrorEvent) for e in collected)  # 不算错误
        assert session.last_role == "assistant"
        done = [e for e in collected if isinstance(e, ChatReplyChunkEvent) and e.done]
        assert done[0].reply == "喵～你好呀"

    def should_stream_in_pieces_when_local_reply_long(self):
        local = FakeResponder("你好呀，我是一只住在桌面上的橘猫，很高兴见到你。")
        service, _, collected, _ = make_service(FakeGateway(), config=AIConfig(api_key=""),
                                                local=local)

        service.send("你好")
        service.process_next()

        deltas = [e.text_delta for e in collected
                  if isinstance(e, ChatReplyChunkEvent) and e.text_delta]
        assert len(deltas) > 1                # 真的逐段出现，不是一下子拍整句
        assert "".join(deltas) == local.fixed

    def should_use_gateway_when_key_configured_even_with_responder(self):
        gateway = FakeGateway(chunks=["AI"])
        local = FakeResponder("本地")
        service, _, _, _ = make_service(gateway, config=AIConfig(api_key="sk-real"),
                                        local=local)

        service.send("你好")
        service.process_next()

        assert gateway.calls and local.texts == []

    def should_keep_error_path_when_no_responder_injected(self):
        """不注入本地引擎时保持旧行为：交给网关，失败发错误事件。"""
        gateway = FakeGateway(error=AIGatewayError(AIGatewayError.AUTH, "api_key 未配置"))
        service, _, collected, _ = make_service(gateway, config=AIConfig(api_key=""))

        service.send("你好")
        service.process_next()

        assert [e.kind for e in collected if isinstance(e, ChatErrorEvent)] == [
            AIGatewayError.AUTH]


class TestErrorMapping:
    @pytest.mark.parametrize("kind", list(AIGatewayError.KINDS))
    def should_publish_error_event_and_fallback_text_when_gateway_fails(self, kind):
        gateway = FakeGateway(error=AIGatewayError(kind, "boom"))
        service, _, collected, session = make_service(gateway)
        service.send("hi")
        service.process_next()
        errors = [e for e in collected if isinstance(e, ChatErrorEvent)]
        assert len(errors) == 1 and errors[0].kind == kind
        assert FALLBACK_TEXTS[kind]  # 每种错误都有可读话术
        assert session.last_role == "user"  # 失败不写入 assistant

    def should_publish_server_error_when_gateway_raises_unexpected(self):
        """非领域异常（驱动崩溃等）也不能打断服务：兜底为 server 错误（PRD 可靠性）。"""
        gateway = FakeGateway(error=RuntimeError("socket blown"))
        service, _, collected, session = make_service(gateway)
        service.send("hi")

        assert service.process_next() is True

        errors = [e for e in collected if isinstance(e, ChatErrorEvent)]
        assert errors[0].kind == AIGatewayError.SERVER
        assert errors[0].detail == "unexpected"
        assert session.last_role == "user"

    def should_cancel_inflight_when_flag_set_midway(self):
        gateway = FakeGateway(chunks=["part1", "part2", "part3"])
        service, bus, collected, session = make_service(gateway)

        def cancel_on_first_chunk(event):
            if event.text_delta == "part1":
                service.cancel_inflight()

        bus.subscribe(ChatReplyChunkEvent, cancel_on_first_chunk)
        service.send("hi")
        service.process_next()
        done = [e for e in collected if isinstance(e, ChatReplyChunkEvent) and e.done]
        assert done[0].reply == "part1"
        assert session.turns[-1].content == "part1"

    def should_finish_immediately_when_cancelled_before_first_chunk(self):
        gateway = FakeGateway(chunks=["a", "b"])
        service, _, collected, _ = make_service(gateway)
        service.send("hi")
        gateway.before_first_chunk = service.cancel_inflight
        service.process_next()
        deltas = [e for e in collected if isinstance(e, ChatReplyChunkEvent) and e.text_delta]
        assert deltas == []  # 首 chunk 到达前已取消，不对外发布


class TestSourceLogging:
    """从日志能分清“这条是模型答的还是本地小脑答的”：两条链路的拟流式气泡长得一样。

    只断言字段存在与数值，不断言 elapsed（真时钟读数）。
    """

    def should_log_model_endpoint_and_counts_when_key_configured(self, caplog):
        gateway = FakeGateway(chunks=["你", "好"])
        service, _, _, _ = make_service(
            gateway, config=AIConfig(api_key="sk-x", base_url="https://api.test/v1",
                                     model="glm-4-flash"))

        with caplog.at_level(logging.INFO):
            service.send("hi")
            service.process_next()

        text = caplog.text
        assert "source=ai request" in text
        assert "model=glm-4-flash" in text
        assert "endpoint=https://api.test/v1/chat/completions" in text
        assert "msgs=2" in text and "prompt_chars=15" in text   # SYSTEM_PROMPT + "hi"
        assert "source=ai done" in text
        assert "chunks=2" in text and "reply_chars=2" in text
        assert "first_chunk=" in text and "elapsed=" in text

    def should_mark_first_chunk_na_when_reply_empty(self, caplog):
        """没拿到一个字时不能把 0.00s 当成“秒回”误导人。"""
        service, _, _, _ = make_service(FakeGateway(chunks=[]))

        with caplog.at_level(logging.INFO):
            service.send("hi")
            service.process_next()

        assert "first_chunk=n/a" in caplog.text

    def should_log_intent_and_touch_no_gateway_when_key_missing(self, caplog):
        gateway = FakeGateway(chunks=["不该被调用"])
        local = FakeResponder("喵～先说给我听", intent="stress")
        service, _, _, _ = make_service(gateway, config=AIConfig(api_key=""), local=local)

        with caplog.at_level(logging.INFO):
            service.send("我心态有点崩")
            service.process_next()

        assert "source=local intent=stress" in caplog.text
        assert "reply_chars=7" in caplog.text
        assert "source=ai" not in caplog.text
        assert gateway.calls == []

    def should_keep_reply_preview_short_in_info_log_when_local(self, caplog):
        """日志只求“能认出是哪一条”：正文截断，气泡里才是全文。"""
        local = FakeResponder("喵～" + "长" * 80)
        service, _, _, _ = make_service(FakeGateway(), config=AIConfig(api_key=""), local=local)

        with caplog.at_level(logging.INFO):
            service.send("我心态有点崩")
            service.process_next()

        assert "先说给我听" not in caplog.text
        assert "preview=" in caplog.text
        line = [r.getMessage() for r in caplog.records if "preview=" in r.getMessage()][-1]
        assert len(line.split("preview=")[1]) == REPLY_PREVIEW_CHARS

    def should_fold_newline_and_keep_one_line_when_preview_has_wraps(self, caplog):
        """一条回复必须只占一行日志，否则 grep 不到上下文。"""
        gateway = FakeGateway(chunks=["第一行\n", "第二行"])
        service, _, _, _ = make_service(gateway)

        with caplog.at_level(logging.INFO):
            service.send("hi")
            service.process_next()

        assert "reply preview: " not in caplog.text   # 不再单独写 DEBUG 行
        assert "第一行↘第二行" in caplog.text

    def should_number_messages_in_order_when_several_queued(self, caplog):
        gateway = FakeGateway(chunks=["x"])
        service, _, _, _ = make_service(gateway)

        with caplog.at_level(logging.INFO):
            service.send("a")
            service.send("b")
            service.process_next()
            service.process_next()

        assert "chat #1 source=ai" in caplog.text
        assert "chat #2 source=ai" in caplog.text

    def should_mark_cancelled_when_cancel_midway(self, caplog):
        gateway = FakeGateway(chunks=["part1", "part2", "part3"])
        service, bus, _, _ = make_service(gateway)

        def cancel_on_first_chunk(event):
            if event.text_delta == "part1":
                service.cancel_inflight()

        bus.subscribe(ChatReplyChunkEvent, cancel_on_first_chunk)
        with caplog.at_level(logging.INFO):
            service.send("hi")
            service.process_next()

        assert "cancelled=True" in caplog.text
        assert "chat #1 source=ai cancelled by user" in caplog.text   # 带编号，能与请求行串起来
        assert "reply_chars=5" in caplog.text      # 只算了已取的 "part1"

    def should_log_error_kind_when_gateway_fails(self, caplog):
        gateway = FakeGateway(error=AIGatewayError(AIGatewayError.PROTOCOL, "msg: 404 NOT_FOUND"))
        service, _, _, _ = make_service(gateway)

        with caplog.at_level(logging.INFO):
            service.send("hi")
            service.process_next()

        assert "source=ai failed [protocol]" in caplog.text
        assert "404 NOT_FOUND" in caplog.text
