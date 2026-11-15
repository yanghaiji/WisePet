"""OpenAI 兼容流式客户端：POST {base_url}/chat/completions（PRD M4）。"""
from __future__ import annotations

import json
import logging
from typing import Iterator

import httpx

from ..domain.errors import AIGatewayError
from ..domain.ports import AIConfig

logger = logging.getLogger(__name__)

_DONE_MARKER = "[DONE]"
_DATA_PREFIX = "data:"

#: 设置窗口「测试连接」探活参数：极小请求，不产生有效计费内容
PROBE_TIMEOUT_S = 10.0
_PROBE_MAX_TOKENS = 1

#: 兼容网关的错误字段名不统一（OpenAI 用 error.message，智谱用 msg/code）
_ERROR_TEXT_FIELDS = ("message", "msg", "error", "detail")
_SSE_CONTENT_TYPE = "text/event-stream"
_PROBE_OK_FIELD = "choices"
#: 错误体只截这么多字写日志，不整块输出响应体
_PREVIEW_CHARS = 200


def _dict_error(obj: dict) -> str:
    """把一个已解析的 JSON 对象翻译成错误描述；返回空串表示看不出错误。"""
    for field in _ERROR_TEXT_FIELDS:
        value = obj.get(field)
        if isinstance(value, dict):
            value = value.get("message") or value.get("msg")
        if isinstance(value, str) and value.strip():
            return f"{field}: {value.strip()}"
    # 智谱风格：{"code":500,"msg":"404 NOT_FOUND","success":false}
    if obj.get("success") is False or str(obj.get("code", "0")) not in ("0", "200"):
        return f"网关返回错误码 {obj.get('code', '未知')}"
    return ""


def _body_error(body: str) -> str:
    """非流式响应体的错误提取；不是 JSON 或看不出错误时返回空串。"""
    text = (body or "").strip()
    if not text.startswith("{"):
        return ""
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return ""
    return _dict_error(obj)


def probe(cfg: AIConfig) -> None:
    """最小非流式对话请求验证连通性；失败抛 AIGatewayError（与正式调用同一分类）。"""
    if not cfg.configured:
        raise AIGatewayError(AIGatewayError.AUTH, "api_key 未配置")
    payload = {
        "model": cfg.model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": _PROBE_MAX_TOKENS,
        "stream": False,
    }
    try:
        with httpx.Client(timeout=PROBE_TIMEOUT_S) as client:
            resp = client.post(cfg.chat_endpoint, json=payload,
                              headers={"Authorization": f"Bearer {cfg.api_key}"})
        _raise_status_code(resp.status_code)
        # 只看状态码会「测试连接成功但聊天没回复」：网关可以把错误包在 200 里
        hint = _body_error(resp.text)
        if hint:
            raise AIGatewayError(AIGatewayError.PROTOCOL, hint)
        if _PROBE_OK_FIELD not in resp.text:
            raise AIGatewayError(
                AIGatewayError.PROTOCOL,
                f"返回体里没有 {_PROBE_OK_FIELD} 字段，Base URL 可能不是 OpenAI 兼容地址")
        logger.info("ai probe ok model=%s endpoint=%s", cfg.model, cfg.chat_endpoint)
    except AIGatewayError:
        raise
    except httpx.TimeoutException as exc:
        raise AIGatewayError(AIGatewayError.TIMEOUT, str(exc) or exc.__class__.__name__) from exc
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
        raise AIGatewayError(AIGatewayError.NETWORK, str(exc)) from exc


def _raise_status_code(status_code: int) -> None:
    """状态码 → 错误分类（与 OpenAICompatClient._raise_for_status 同一语义）。"""
    if status_code in (401, 403):
        raise AIGatewayError(AIGatewayError.AUTH, f"HTTP {status_code}")
    if status_code == 429:
        raise AIGatewayError(AIGatewayError.RATE, "HTTP 429 限流")
    if status_code >= 500 or not 200 <= status_code < 300:
        raise AIGatewayError(AIGatewayError.SERVER, f"HTTP {status_code}")


class OpenAICompatClient:
    """实现 domain.ports.AIChatGateway 协议。

    错误分类映射（PRD M4 异常场景）：
    401/403 → auth；429 → rate；5xx/其他非2xx → server；
    超时 → timeout；连接/URL 非法等传输错误 → network；
    2xx 但回读不出内容（错误包在 200 里 / 非 SSE / 空流）→ protocol。
    """

    def stream_chat(self, messages: list[dict], cfg: AIConfig) -> Iterator[str]:
        if not cfg.configured:
            raise AIGatewayError(AIGatewayError.AUTH, "api_key 未配置")
        payload = {
            "model": cfg.model,
            "messages": messages,
            "stream": True,
            "temperature": cfg.temperature,
        }
        headers = {
            "Authorization": f"Bearer {cfg.api_key}",
            "Accept": "text/event-stream",
        }
        try:
            with httpx.Client(timeout=cfg.timeout_s) as client:
                with client.stream("POST", cfg.chat_endpoint,
                                   json=payload, headers=headers) as resp:
                    self._raise_for_status(resp)
                    content_type = resp.headers.get("content-type") or ""
                    if content_type and _SSE_CONTENT_TYPE not in content_type:
                        # 200 但不是流：要么网关把错误包在体里，要么 Base URL 指向了别的协议
                        body = resp.read().decode("utf-8", "replace")
                        logger.warning("non-SSE chat response (content-type=%s): %.*s",
                                       content_type, _PREVIEW_CHARS, body)
                        raise AIGatewayError(
                            AIGatewayError.PROTOCOL,
                            _body_error(body) or f"响应不是流式（content-type={content_type}）")
                    emitted = 0
                    for chunk in self._iter_sse(resp):
                        emitted += 1
                        yield chunk
                    if emitted == 0:
                        # 一段内容都没解析出来 = 用户看到的是“没回复”，必须当成错误报出去
                        raise AIGatewayError(
                            AIGatewayError.PROTOCOL,
                            "流式响应里没有解析到文本内容（缺少 choices[0].delta.content）")
        except AIGatewayError:
            raise
        except httpx.TimeoutException as exc:
            raise AIGatewayError(AIGatewayError.TIMEOUT, str(exc) or exc.__class__.__name__) from exc
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
            raise AIGatewayError(AIGatewayError.NETWORK, str(exc)) from exc

    def complete(self, messages: list[dict], cfg: AIConfig) -> str:
        """非流式对话：后台任务（新闻摘要等）用，一次拿全文。"""
        content, _calls = self.chat_with_tools(messages, cfg, [])
        return content

    def chat_with_tools(self, messages: list[dict], cfg: AIConfig,
                        tools: list[dict]) -> tuple[str, list[dict]]:
        """非流式带工具定义（OpenAI tools 协议）：返回 (content, tool_calls)。

        content 可为空（模型只请求工具）；tool_calls 已把 arguments 从 JSON
        字符串解析成 dict。网关不支持 tools（400/422/协议错误）时抛
        AIGatewayError.PROTOCOL，由上层降级回纯文本协议。
        """
        if not cfg.configured:
            raise AIGatewayError(AIGatewayError.AUTH, "api_key 未配置")
        payload = {
            "model": cfg.model,
            "messages": messages,
            "stream": False,
            "temperature": cfg.temperature,
        }
        if tools:                              # 空 tools 不下发：兼容挑剔的网关
            payload["tools"] = list(tools)
            payload["tool_choice"] = "auto"
        headers = {"Authorization": f"Bearer {cfg.api_key}"}
        try:
            with httpx.Client(timeout=cfg.timeout_s) as client:
                resp = client.post(cfg.chat_endpoint, json=payload, headers=headers)
            _raise_status_code(resp.status_code)
            obj = json.loads(resp.text)
            if not isinstance(obj, dict):
                raise AIGatewayError(AIGatewayError.PROTOCOL, "响应不是 JSON 对象")
            hint = _dict_error(obj)
            if hint:
                raise AIGatewayError(AIGatewayError.PROTOCOL, hint)
            choices = obj.get("choices") or []
            message = (choices[0].get("message") or {}) if choices else {}
            content = str(message.get("content") or "")
            calls: list[dict] = []
            for index, call in enumerate(message.get("tool_calls") or []):
                fn = call.get("function") or {}
                raw_args = fn.get("arguments")
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError as exc:
                    raise AIGatewayError(
                        AIGatewayError.PROTOCOL,
                        f"tool arguments 不是 JSON：{str(raw_args)[:80]}") from exc
                if not isinstance(args, dict):
                    raise AIGatewayError(AIGatewayError.PROTOCOL,
                                         "tool arguments 不是 JSON 对象")
                calls.append({"id": str(call.get("id") or f"call-{index}"),
                              "name": str(fn.get("name") or ""),
                              "arguments": args})
            if not content and not calls:
                raise AIGatewayError(AIGatewayError.PROTOCOL,
                                     "响应里既没有 content 也没有 tool_calls")
            return content, calls
        except AIGatewayError:
            raise
        except json.JSONDecodeError as exc:
            raise AIGatewayError(AIGatewayError.PROTOCOL, f"响应不是 JSON：{exc}") from exc
        except httpx.TimeoutException as exc:
            raise AIGatewayError(AIGatewayError.TIMEOUT, str(exc) or exc.__class__.__name__) from exc
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
            raise AIGatewayError(AIGatewayError.NETWORK, str(exc)) from exc

    def chat_with_tools_stream(self, messages: list[dict], cfg: AIConfig,
                               tools: list[dict],
                               on_content=None) -> tuple[str, list[dict]]:
        """流式带工具定义：content 增量实时回调 on_content(delta)，返回全文与工具调用。

        agent 循环的最后一轮（模型不再请求工具）通过回调拿到逐字增量，
        上层据此真流式上屏，消除「工具循环跑完才一次性出字」的卡顿感。
        delta.tool_calls 按分片聚合（arguments 可能分多个 SSE 片到达）；
        流式不可用/被网关拒绝时抛 AIGatewayError，由调用方回落非流式。
        """
        if not cfg.configured:
            raise AIGatewayError(AIGatewayError.AUTH, "api_key 未配置")
        payload = {
            "model": cfg.model,
            "messages": messages,
            "stream": True,
            "temperature": cfg.temperature,
        }
        if tools:
            payload["tools"] = list(tools)
            payload["tool_choice"] = "auto"
        headers = {"Authorization": f"Bearer {cfg.api_key}",
                   "Accept": _SSE_CONTENT_TYPE}
        content_parts: list[str] = []
        # index → {id, name, arguments}：OpenAI 流式协议按 index 分片聚合
        call_slots: dict[int, dict] = {}
        try:
            with httpx.Client(timeout=cfg.timeout_s) as client:
                with client.stream("POST", cfg.chat_endpoint,
                                   json=payload, headers=headers) as resp:
                    self._raise_for_status(resp)
                    content_type = resp.headers.get("content-type") or ""
                    if content_type and _SSE_CONTENT_TYPE not in content_type:
                        body = resp.read().decode("utf-8", "replace")
                        raise AIGatewayError(
                            AIGatewayError.PROTOCOL,
                            _body_error(body) or f"响应不是流式（content-type={content_type}）")
                    for line in resp.iter_lines():
                        line = (line or "").strip()
                        if not line.startswith(_DATA_PREFIX):
                            continue
                        data = line[len(_DATA_PREFIX):].strip()
                        if data == _DONE_MARKER:
                            break
                        if not data:
                            continue
                        try:
                            obj = json.loads(data)
                        except json.JSONDecodeError:
                            logger.warning("skip malformed SSE payload: %.80s", data)
                            continue
                        if not isinstance(obj, dict):
                            continue
                        hint = _dict_error(obj)
                        if hint:
                            raise AIGatewayError(AIGatewayError.PROTOCOL, hint)
                        choices = obj.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        text = delta.get("content")
                        if text:
                            content_parts.append(text)
                            if on_content is not None:
                                on_content(text)
                        for tc in delta.get("tool_calls") or []:
                            try:
                                index = int(tc.get("index") or 0)
                            except (TypeError, ValueError):
                                index = 0
                            slot = call_slots.setdefault(
                                index, {"id": "", "name": "", "arguments": ""})
                            if tc.get("id"):
                                slot["id"] = str(tc["id"])
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["name"] += str(fn["name"])
                            if fn.get("arguments"):
                                slot["arguments"] += str(fn["arguments"])
        except AIGatewayError:
            raise
        except httpx.TimeoutException as exc:
            raise AIGatewayError(AIGatewayError.TIMEOUT, str(exc) or exc.__class__.__name__) from exc
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
            raise AIGatewayError(AIGatewayError.NETWORK, str(exc)) from exc
        content = "".join(content_parts)
        calls: list[dict] = []
        for index, slot in sorted(call_slots.items()):
            raw = slot["arguments"]
            try:
                args = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError as exc:
                raise AIGatewayError(
                    AIGatewayError.PROTOCOL,
                    f"流式 tool arguments 不是 JSON：{raw[:80]}") from exc
            if not isinstance(args, dict):
                raise AIGatewayError(AIGatewayError.PROTOCOL,
                                     "tool arguments 不是 JSON 对象")
            calls.append({"id": slot["id"] or f"call-{index}",
                          "name": slot["name"],
                          "arguments": args})
        if not content and not calls:
            raise AIGatewayError(
                AIGatewayError.PROTOCOL,
                "流式响应里既没有 content 也没有 tool_calls")
        return content, calls

    def embeddings(self, texts: list[str], cfg: AIConfig) -> list[list[float]]:
        """OpenAI 兼容 embeddings 批量调用：返回与 texts 等长的向量列表。

        资料索引的语义检索用（v1.31）。错误分类与 complete 一致；空输入直接返回空。
        """
        texts = [str(t or "") for t in (texts or [])]
        if not texts:
            return []
        if not cfg.api_key.strip() or not cfg.embedding_model.strip():
            raise AIGatewayError(AIGatewayError.AUTH,
                                 "语义检索需要 api_key 与 embedding_model")
        payload = {"model": cfg.embedding_model, "input": texts}
        headers = {"Authorization": f"Bearer {cfg.api_key}"}
        try:
            with httpx.Client(timeout=cfg.timeout_s) as client:
                resp = client.post(cfg.embedding_endpoint, json=payload,
                                   headers=headers)
            _raise_status_code(resp.status_code)
            obj = json.loads(resp.text)
            if not isinstance(obj, dict):
                raise AIGatewayError(AIGatewayError.PROTOCOL, "响应不是 JSON 对象")
            hint = _dict_error(obj)
            if hint:
                raise AIGatewayError(AIGatewayError.PROTOCOL, hint)
            data = obj.get("data") or []
            vectors = []
            for item in sorted(data, key=lambda d: d.get("index", 0)):
                vec = item.get("embedding")
                if not isinstance(vec, list) or not vec:
                    raise AIGatewayError(AIGatewayError.PROTOCOL,
                                         "embeddings 返回缺少向量字段")
                vectors.append([float(x) for x in vec])
            if len(vectors) != len(texts):
                raise AIGatewayError(AIGatewayError.PROTOCOL,
                                     f"向量数量不匹配：{len(vectors)}/{len(texts)}")
            return vectors
        except AIGatewayError:
            raise
        except json.JSONDecodeError as exc:
            raise AIGatewayError(AIGatewayError.PROTOCOL, f"响应不是 JSON：{exc}") from exc
        except httpx.TimeoutException as exc:
            raise AIGatewayError(AIGatewayError.TIMEOUT, str(exc) or exc.__class__.__name__) from exc
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
            raise AIGatewayError(AIGatewayError.NETWORK, str(exc)) from exc

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        _raise_status_code(resp.status_code)

    @staticmethod
    def _iter_sse(resp: httpx.Response) -> Iterator[str]:
        """解析 SSE 流，逐段 yield delta.content；体内带错误字段时抛 PROTOCOL。"""
        for line in resp.iter_lines():
            line = (line or "").strip()
            if not line.startswith(_DATA_PREFIX):
                continue  # 注释/心跳/空行
            data = line[len(_DATA_PREFIX):].strip()
            if data == _DONE_MARKER:
                return
            if not data:
                continue
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                logger.warning("skip malformed SSE payload: %.80s", data)
                continue
            if not isinstance(obj, dict):
                continue
            hint = _dict_error(obj)          # 部分网关把错误放在 SSE 体里，状态仍是 200
            if hint:
                raise AIGatewayError(AIGatewayError.PROTOCOL, hint)
            choices = obj.get("choices") or []
            if not choices:
                continue
            content = (choices[0].get("delta") or {}).get("content")
            if content:
                yield content
