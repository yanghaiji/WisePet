"""OpenAICompatClient 集成测试：SSE 流解析、错误分类、连接探活（respx mock）。"""
import json
import logging

import httpx
import pytest
import respx

from WisePet.domain.errors import AIGatewayError
from WisePet.domain.ports import AIConfig
from WisePet.infra.openai_client import PROBE_TIMEOUT_S, OpenAICompatClient, probe

BASE = "https://api.test.local/v1"
ENDPOINT = f"{BASE}/chat/completions"

SSE_TWO_CHUNKS = (
    b'data: {"choices":[{"delta":{"content":"He"}}]}\n\n'
    b'data: {"choices":[{"delta":{"content":"llo"}}]}\n\n'
    b"data: [DONE]\n\n"
)

#: 真网关会带 content-type；缺省时客户端不拦（兼容省略该头的服务）
SSE_HEADERS = {"content-type": "text/event-stream;charset=utf-8"}


def _cfg(**overrides):
    defaults = dict(base_url=BASE, api_key="sk-test", model="test-model", timeout_s=5.0)
    defaults.update(overrides)
    return AIConfig(**defaults)


def _client():
    return OpenAICompatClient()


MESSAGES = [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}]


class TestStreaming:
    @respx.mock
    def should_yield_deltas_when_sse_ok(self):
        respx.post(ENDPOINT).mock(return_value=httpx.Response(200, content=SSE_TWO_CHUNKS))
        chunks = list(_client().stream_chat(MESSAGES, _cfg()))
        assert chunks == ["He", "llo"]

    @respx.mock
    def should_skip_heartbeat_and_empty_lines_when_streaming(self):
        body = b": ping\n\ndata: \ndata: {\"choices\":[{\"delta\":{\"content\":\"x\"}}]}\n\ndata: [DONE]\n\n"
        respx.post(ENDPOINT).mock(return_value=httpx.Response(200, content=body))
        assert list(_client().stream_chat(MESSAGES, _cfg())) == ["x"]

    @respx.mock
    def should_send_auth_and_stream_flag_when_requesting(self):
        route = respx.post(ENDPOINT).mock(
            return_value=httpx.Response(200, content=b'data: {"choices":[{"delta":{"content":"x"}}]}\n\ndata: [DONE]\n\n'))

        assert list(_client().stream_chat(MESSAGES, _cfg())) == ["x"]

        req = route.calls[0].request
        assert req.headers["authorization"] == "Bearer sk-test"
        payload = json.loads(req.content)
        assert payload["stream"] is True
        assert payload["model"] == "test-model"
        assert payload["messages"] == MESSAGES

    @respx.mock
    def should_skip_malformed_and_empty_choice_payloads_when_streaming(self):
        body = (b'data: {not json at all}\n\n'
                b'data: [1, 2, 3]\n\n'
                b'data: {"choices": []}\n\n'
                b'data: {"choices":[{"delta":{}}]}\n\n'
                b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
                b'data: [DONE]\n\n')
        respx.post(ENDPOINT).mock(return_value=httpx.Response(200, content=body, headers=SSE_HEADERS))

        assert list(_client().stream_chat(MESSAGES, _cfg())) == ["ok"]

    @respx.mock
    def should_raise_protocol_when_stream_has_no_content(self):
        """一段文本都没解析出来不能等于「静默」：用户会以为程序卡住（NFR v1.6）。

        旧行为是 yield 空序列 -> 气泡不出现，现在必须报错。
        """
        respx.post(ENDPOINT).mock(
            return_value=httpx.Response(200, content=b"data: [DONE]\n\n", headers=SSE_HEADERS))

        with pytest.raises(AIGatewayError) as e:
            list(_client().stream_chat(MESSAGES, _cfg()))

        assert e.value.kind == AIGatewayError.PROTOCOL
        assert "choices" in e.value.detail

    @respx.mock
    def should_raise_protocol_when_status_200_but_body_is_json_error(self):
        """实现在智谱 /api/anthropic 端点上：状态 200，体内是 {"code":500,"msg":"404 NOT_FOUND"}。"""
        respx.post(ENDPOINT).mock(return_value=httpx.Response(
            200, json={"code": 500, "msg": "404 NOT_FOUND", "success": False},
            headers={"content-type": "application/json"}))

        with pytest.raises(AIGatewayError) as e:
            list(_client().stream_chat(MESSAGES, _cfg()))

        assert e.value.kind == AIGatewayError.PROTOCOL
        assert "404 NOT_FOUND" in e.value.detail

    @respx.mock
    def should_raise_protocol_when_status_200_but_body_is_plain_text(self):
        respx.post(ENDPOINT).mock(return_value=httpx.Response(
            200, content=b"<html>maintenance</html>", headers={"content-type": "text/html"}))

        with pytest.raises(AIGatewayError) as e:
            list(_client().stream_chat(MESSAGES, _cfg()))

        assert e.value.kind == AIGatewayError.PROTOCOL
        assert "不是流式" in e.value.detail

    @respx.mock
    def should_raise_protocol_when_sse_payload_carries_error(self):
        body = b'data: {"error":{"code":"invalid_api_key","message":"key is wrong"}}\n\n'
        respx.post(ENDPOINT).mock(return_value=httpx.Response(200, content=body, headers=SSE_HEADERS))

        with pytest.raises(AIGatewayError) as e:
            list(_client().stream_chat(MESSAGES, _cfg()))

        assert e.value.kind == AIGatewayError.PROTOCOL
        assert "key is wrong" in e.value.detail


class TestErrorMapping:
    @respx.mock
    @pytest.mark.parametrize("status", [401, 403])
    def should_raise_auth_when_status_401_403(self, status):
        respx.post(ENDPOINT).mock(return_value=httpx.Response(status))
        with pytest.raises(AIGatewayError) as e:
            list(_client().stream_chat(MESSAGES, _cfg()))
        assert e.value.kind == AIGatewayError.AUTH

    @respx.mock
    def should_raise_rate_when_429(self):
        respx.post(ENDPOINT).mock(return_value=httpx.Response(429))
        with pytest.raises(AIGatewayError) as e:
            list(_client().stream_chat(MESSAGES, _cfg()))
        assert e.value.kind == AIGatewayError.RATE

    @respx.mock
    def should_raise_server_when_5xx(self):
        respx.post(ENDPOINT).mock(return_value=httpx.Response(503))
        with pytest.raises(AIGatewayError) as e:
            list(_client().stream_chat(MESSAGES, _cfg()))
        assert e.value.kind == AIGatewayError.SERVER

    @respx.mock
    def should_raise_timeout_when_read_timeout(self):
        respx.post(ENDPOINT).mock(side_effect=httpx.ReadTimeout("slow"))
        with pytest.raises(AIGatewayError) as e:
            list(_client().stream_chat(MESSAGES, _cfg()))
        assert e.value.kind == AIGatewayError.TIMEOUT

    @respx.mock
    def should_raise_network_when_connect_error(self):
        respx.post(ENDPOINT).mock(side_effect=httpx.ConnectError("down"))
        with pytest.raises(AIGatewayError) as e:
            list(_client().stream_chat(MESSAGES, _cfg()))
        assert e.value.kind == AIGatewayError.NETWORK

    def should_raise_auth_when_api_key_missing(self):
        with pytest.raises(AIGatewayError) as e:
            list(_client().stream_chat(MESSAGES, _cfg(api_key="")))
        assert e.value.kind == AIGatewayError.AUTH

    def should_raise_network_when_base_url_invalid(self):
        # 非法 URL 在协议层被拒，不会发起真实网络请求，无需 respx mock
        with pytest.raises(AIGatewayError) as e:
            list(_client().stream_chat(MESSAGES, _cfg(base_url="not-a-url")))
        assert e.value.kind == AIGatewayError.NETWORK


class TestProbe:
    """设置窗「测试连接」：极小非流式请求，不产生有效计费内容。"""

    @respx.mock
    def should_raise_auth_without_request_when_key_missing(self):
        # 未配置 key 时不得发起任何 HTTP 请求（respx 未注册路由，误发起会报错）
        with pytest.raises(AIGatewayError) as e:
            probe(_cfg(api_key=""))

        assert e.value.kind == AIGatewayError.AUTH

    @respx.mock
    def should_send_minimal_non_stream_request_when_probing(self):
        route = respx.post(ENDPOINT).mock(
            return_value=httpx.Response(200, json={"choices": []}))

        probe(_cfg())

        payload = json.loads(route.calls[0].request.content)
        assert payload["stream"] is False
        assert payload["messages"][0]["content"] == "ping"
        assert route.calls[0].request.headers["authorization"] == "Bearer sk-test"

    @respx.mock
    def should_log_model_and_endpoint_when_probe_succeeds(self, caplog):
        """成功也要留痕：用户要能确认「测试连接」真的打到了自己填的地址。"""
        respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json={"choices": []}))

        with caplog.at_level(logging.INFO):
            probe(_cfg())

        assert "ai probe ok model=test-model" in caplog.text
        assert f"endpoint={ENDPOINT}" in caplog.text

    @respx.mock
    @pytest.mark.parametrize("status,kind", [
        (401, AIGatewayError.AUTH),
        (403, AIGatewayError.AUTH),
        (429, AIGatewayError.RATE),
        (500, AIGatewayError.SERVER),
        (302, AIGatewayError.SERVER),
    ])
    def should_classify_error_when_status_not_ok(self, status, kind):
        respx.post(ENDPOINT).mock(return_value=httpx.Response(status))

        with pytest.raises(AIGatewayError) as e:
            probe(_cfg())

        assert e.value.kind == kind

    @respx.mock
    def should_raise_protocol_when_probe_body_is_error_under_200(self):
        """只查状态码会让「测试连接成功」与「聊天没回复」并存，必须看体。"""
        respx.post(ENDPOINT).mock(return_value=httpx.Response(
            200, json={"code": 500, "msg": "404 NOT_FOUND", "success": False}))

        with pytest.raises(AIGatewayError) as e:
            probe(_cfg())

        assert e.value.kind == AIGatewayError.PROTOCOL
        assert "404 NOT_FOUND" in e.value.detail

    @respx.mock
    def should_raise_protocol_when_probe_body_has_no_choices(self):
        respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json={"ok": True}))

        with pytest.raises(AIGatewayError) as e:
            probe(_cfg())

        assert e.value.kind == AIGatewayError.PROTOCOL
        assert "Base URL" in e.value.detail

    @respx.mock
    def should_raise_protocol_when_probe_body_is_truncated_json(self):
        """以 `{` 开头但不是合法 JSON：不能当成“没错误”放过。"""
        respx.post(ENDPOINT).mock(return_value=httpx.Response(
            200, content=b"{ not json", headers={"content-type": "application/json"}))

        with pytest.raises(AIGatewayError) as e:
            probe(_cfg())

        assert e.value.kind == AIGatewayError.PROTOCOL

    @respx.mock
    def should_report_error_code_when_body_has_no_message_field(self):
        respx.post(ENDPOINT).mock(return_value=httpx.Response(
            200, json={"code": 500, "success": False},
            headers={"content-type": "application/json"}))

        with pytest.raises(AIGatewayError) as e:
            list(_client().stream_chat(MESSAGES, _cfg()))

        assert e.value.kind == AIGatewayError.PROTOCOL
        assert e.value.detail == "网关返回错误码 500"

    @respx.mock
    def should_raise_timeout_when_probe_hangs(self):
        respx.post(ENDPOINT).mock(side_effect=httpx.ReadTimeout("slow"))

        with pytest.raises(AIGatewayError) as e:
            probe(_cfg())

        assert e.value.kind == AIGatewayError.TIMEOUT

    @respx.mock
    def should_raise_network_when_connect_fails(self):
        respx.post(ENDPOINT).mock(side_effect=httpx.ConnectError("down"))

        with pytest.raises(AIGatewayError) as e:
            probe(_cfg())

        assert e.value.kind == AIGatewayError.NETWORK

    def should_raise_network_when_base_url_invalid(self):
        with pytest.raises(AIGatewayError) as e:
            probe(_cfg(base_url="not-a-url"))

        assert e.value.kind == AIGatewayError.NETWORK

    def should_use_short_probe_timeout_when_probing(self):
        # 探活不能沿用对话的 30s 超时，否则设置窗卡死
        assert PROBE_TIMEOUT_S < 30.0
