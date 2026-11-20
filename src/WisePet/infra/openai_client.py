"""OpenAI 兼容流式客户端：POST {base_url}/chat/completions（PRD M4）。"""
from __future__ import annotations

import base64
import json
import time
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
        _raise_status_code(resp.status_code, resp.text)
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


def _status_detail(status_code: int, body: str = "") -> str:
    """HTTP 状态 + 响应体里的错误说明（400 的真实原因几乎都在 body 里）。

    之前只写「HTTP 400」，用户和日志都看不到是模型名错、参数不支持还是别的，
    排查全靠猜。这里把网关返回的错误消息带出来（截断，不整块输出响应体）。
    """
    hint = _body_error(body)
    base = f"HTTP {status_code}"
    return f"{base}: {hint}" if hint else base


def _raise_status_code(status_code: int, body: str = "") -> None:
    """状态码 → 错误分类（与 OpenAICompatClient._raise_for_status 同一语义）。

    400/422：消息体若明确说不支持 tools 等协议问题，归 PROTOCOL 让上层降级；
    其余保持 SERVER，但 detail 一律带上响应体错误说明。
    """
    detail = _status_detail(status_code, body)
    if status_code in (401, 403):
        raise AIGatewayError(AIGatewayError.AUTH, detail)
    if status_code == 429:
        raise AIGatewayError(AIGatewayError.RATE, "HTTP 429 限流")
    if status_code in (400, 422):
        # 模型名拼错/参数非法也是 400，但那不是「网关不支持 tools」；
        # 仅当错误体明确提到 tools/function 才归 PROTOCOL 触发降级。
        lowered = detail.lower()
        if any(word in lowered for word in ("tool", "function_call", "function call")):
            raise AIGatewayError(AIGatewayError.PROTOCOL, detail)
        raise AIGatewayError(AIGatewayError.SERVER, detail)
    if status_code >= 500 or not 200 <= status_code < 300:
        raise AIGatewayError(AIGatewayError.SERVER, detail)


class OpenAICompatClient:
    """实现 domain.ports.AIChatGateway 协议。

    错误分类映射（PRD M4 异常场景）：
    401/403 → auth；429 → rate；5xx/其他非2xx → server；
    超时 → timeout；连接/URL 非法等传输错误 → network；
    2xx 但回读不出内容（错误包在 200 里 / 非 SSE / 空流）→ protocol。
    """

    def stream_chat(self, messages: list[dict], cfg: AIConfig,
                    on_reasoning=None) -> Iterator[str]:
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
                    seen: list[str] = []
                    def _collect_reasoning(part: str) -> None:
                        seen.append(part)
                        if on_reasoning is not None:
                            on_reasoning(part)
                    for chunk in self._iter_sse(resp, on_reasoning=_collect_reasoning):
                        emitted += 1
                        yield chunk
                    if emitted == 0 and not seen:
                        # 正文和思考都没解析出来 = 用户看到的是“没回复”，必须报错
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
                        tools: list[dict],
                        on_reasoning=None) -> tuple[str, list[dict]]:
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
            _raise_status_code(resp.status_code, resp.text)
            obj = json.loads(resp.text)
            if not isinstance(obj, dict):
                raise AIGatewayError(AIGatewayError.PROTOCOL, "响应不是 JSON 对象")
            hint = _dict_error(obj)
            if hint:
                # 网关把「不支持 tools」包在 200 里时也要触发上层降级
                kind = (AIGatewayError.PROTOCOL
                        if ("tool" in hint.lower() or "function_call" in hint.lower())
                        else AIGatewayError.SERVER)
                raise AIGatewayError(kind, hint)
            choices = obj.get("choices") or []
            message = (choices[0].get("message") or {}) if choices else {}
            reasoning = message.get("reasoning_content") or message.get("reasoning")
            if reasoning and on_reasoning is not None:
                try:
                    on_reasoning(str(reasoning))
                except Exception:  # noqa: BLE001 - 展示增强不崩调用
                    logger.debug("reasoning callback failed", exc_info=True)
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
                               on_content=None,
                               on_reasoning=None) -> tuple[str, list[dict]]:
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
                        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                        if reasoning and on_reasoning is not None:
                            try:
                                on_reasoning(str(reasoning))
                            except Exception:  # noqa: BLE001 - 展示增强不崩流
                                logger.debug("reasoning callback failed", exc_info=True)
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

    def generate_image(self, prompt: str, cfg: AIConfig,
                       size: str = "1024x1024") -> tuple[bytes, str]:
        """OpenAI 兼容图像生成：POST {base_url}/images/generations。

        兼容两种返回：data[0].b64_json（首选，免二次请求）或 data[0].url
        （再 GET 拿字节）。返回 (图片字节, 媒体类型)；失败抛 AIGatewayError。
        图像生成通常比对话慢，这里放宽超时到 max(timeout_s, 120)。
        """
        if not cfg.configured:
            raise AIGatewayError(AIGatewayError.AUTH, "api_key 未配置")
        payload = {"model": cfg.model, "prompt": str(prompt or ""),
                   "n": 1, "size": size, "response_format": "b64_json"}
        headers = {"Authorization": f"Bearer {cfg.api_key}"}
        timeout = max(float(cfg.timeout_s), 120.0)
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(cfg.image_endpoint, json=payload, headers=headers)
            _raise_status_code(resp.status_code, resp.text)
            obj = json.loads(resp.text)
            if not isinstance(obj, dict):
                raise AIGatewayError(AIGatewayError.PROTOCOL, "图像响应不是 JSON 对象")
            hint = _dict_error(obj)
            if hint:
                raise AIGatewayError(AIGatewayError.SERVER, hint)
            data = obj.get("data") or []
            if not isinstance(data, list) or not data:
                raise AIGatewayError(AIGatewayError.PROTOCOL, "图像响应里没有 data")
            first = data[0] if isinstance(data[0], dict) else {}
            b64 = first.get("b64_json")
            if isinstance(b64, str) and b64.strip():
                try:
                    return base64.b64decode(b64), "image/png"
                except Exception as exc:  # noqa: BLE001 - 坏 base64 归协议错误
                    raise AIGatewayError(AIGatewayError.PROTOCOL,
                                         f"图像 b64_json 解码失败：{exc}") from exc
            url = str(first.get("url") or "")
            if not url:
                raise AIGatewayError(AIGatewayError.PROTOCOL,
                                     "图像响应既没有 b64_json 也没有 url")
            # 网关只给了 URL：再 GET 一次拿字节（不落地远程引用，隐私口径）
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                img = client.get(url)
            _raise_status_code(img.status_code, img.text[:200] if img.content else "")
            media = (img.headers.get("content-type") or "image/png").split(";", 1)[0]
            return img.content, media
        except AIGatewayError:
            raise
        except json.JSONDecodeError as exc:
            raise AIGatewayError(AIGatewayError.PROTOCOL, f"图像响应不是 JSON：{exc}") from exc
        except httpx.TimeoutException as exc:
            raise AIGatewayError(AIGatewayError.TIMEOUT, str(exc) or exc.__class__.__name__) from exc
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
            raise AIGatewayError(AIGatewayError.NETWORK, str(exc)) from exc

    def generate_video(self, prompt: str, cfg: AIConfig,
                       progress=None) -> tuple[bytes, str]:
        """异步视频生成：POST /videos 建任务 → 轮询 /agnesapi → 下载成片。

        Agnes Video 走 OpenAI Videos 兼容协议但状态查询在 host 根目录；不同网关
        端点可能略有差异，这里对返回字段做宽容解析（id/task_id/video_id 都认）。
        progress(text) 把排队/生成进度送到 UI；失败抛 AIGatewayError。
        """
        if not cfg.configured:
            raise AIGatewayError(AIGatewayError.AUTH, "api_key 未配置")
        payload = {"model": cfg.model, "prompt": str(prompt or ""),
                   "mode": "text", "seconds": "5", "size": "720P",
                   "aspect_ratio": "16:9", "n": 1}
        headers = {"Authorization": f"Bearer {cfg.api_key}",
                   "Content-Type": "application/json"}
        timeout = max(float(cfg.timeout_s), 60.0)
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(cfg.video_endpoint, json=payload, headers=headers)
            _raise_status_code(resp.status_code, resp.text)
            obj = json.loads(resp.text)
            if not isinstance(obj, dict):
                raise AIGatewayError(AIGatewayError.PROTOCOL, "视频响应不是 JSON 对象")
            hint = _dict_error(obj)
            if hint:
                raise AIGatewayError(AIGatewayError.SERVER, hint)
            video_id = str(obj.get("video_id") or obj.get("id")
                           or obj.get("task_id") or "").strip()
            if not video_id:
                raise AIGatewayError(AIGatewayError.PROTOCOL,
                                     "视频响应里没有 video_id/task_id")

            # 轮询：3 秒一次（2 秒太频繁，国内网关实测第 2 次就 429），最多 5 分钟。
            # 429 不是任务失败，只是查得太快——退避后重试，任务仍在服务端跑。
            deadline = time.monotonic() + 300.0
            status_url = cfg.video_status_endpoint
            if progress:
                progress("视频任务已提交，排队中…")
            backoff = 3.0
            while time.monotonic() < deadline:
                time.sleep(backoff)
                with httpx.Client(timeout=timeout) as client:
                    poll = client.get(
                        status_url,
                        params={"video_id": video_id, "model_name": cfg.model},
                        headers={"Authorization": f"Bearer {cfg.api_key}"})
                if poll.status_code == 429:
                    # 尊重 Retry-After，否则指数退避（3→6→12→24，封顶 30 秒）
                    retry_after = poll.headers.get("retry-after", "")
                    try:
                        wait = float(retry_after) if retry_after else backoff
                    except ValueError:
                        wait = backoff
                    backoff = min(30.0, max(3.0, wait, backoff * 2))
                    if progress:
                        progress(f"查进度时被限流，{int(backoff)} 秒后再看…")
                    continue
                # 成功查到状态，恢复正常间隔
                backoff = 3.0
                _raise_status_code(poll.status_code, poll.text)
                state = json.loads(poll.text)
                if not isinstance(state, dict):
                    raise AIGatewayError(AIGatewayError.PROTOCOL, "视频状态响应不是 JSON 对象")
                status = str(state.get("status") or "").lower()
                pct = state.get("progress")
                if status == "completed":
                    url = str(state.get("url") or "")
                    if not url:
                        raise AIGatewayError(AIGatewayError.PROTOCOL,
                                             "视频完成但响应里没有 url")
                    if progress:
                        progress("视频生成完成，下载中…")
                    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                        movie = client.get(url)
                    _raise_status_code(movie.status_code,
                                       movie.text[:200] if movie.content else "")
                    media = (movie.headers.get("content-type") or "video/mp4").split(";", 1)[0]
                    return movie.content, media
                if status == "failed":
                    err = state.get("error") or {}
                    msg = err.get("message") if isinstance(err, dict) else str(err)
                    raise AIGatewayError(AIGatewayError.SERVER,
                                         f"视频生成失败：{msg or '未知原因'}")
                if progress:
                    detail = f" {int(pct)}%" if isinstance(pct, (int, float)) else ""
                    label = "生成中" if status in ("in_progress", "processing") else "排队中"
                    progress(f"视频{label}{detail}…")
            raise AIGatewayError(AIGatewayError.TIMEOUT, "视频生成超过 5 分钟未完成")
        except AIGatewayError:
            raise
        except json.JSONDecodeError as exc:
            raise AIGatewayError(AIGatewayError.PROTOCOL, f"视频响应不是 JSON：{exc}") from exc
        except httpx.TimeoutException as exc:
            raise AIGatewayError(AIGatewayError.TIMEOUT, str(exc) or exc.__class__.__name__) from exc
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
            raise AIGatewayError(AIGatewayError.NETWORK, str(exc)) from exc

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
            _raise_status_code(resp.status_code, resp.text)
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
        # 流式响应在报错时读一次 body 拿错误说明；成功时不读，避免破坏 SSE 流。
        body = ""
        if resp.status_code < 200 or resp.status_code >= 300:
            try:
                body = resp.read().decode("utf-8", "replace")
            except httpx.HTTPError:
                body = ""
        _raise_status_code(resp.status_code, body)

    @staticmethod
    def _iter_sse(resp: httpx.Response, on_reasoning=None) -> Iterator[str]:
        """解析 SSE 流，逐段 yield delta.content；体内带错误字段时抛 PROTOCOL。

        推理模型（如 agnes-3.0-flash）会在 delta.reasoning_content 里返回思考过程。
        有 on_reasoning 回调就把思考段送出去（不混进正文），没有则忽略——完全向后兼容。
        """
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
            delta = choices[0].get("delta") or {}
            # 思考过程：与正文分开送，不混进给用户的回复
            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
            if reasoning and on_reasoning is not None:
                try:
                    on_reasoning(str(reasoning))
                except Exception:  # noqa: BLE001 - 展示是增强，别让回调搞崩流
                    logger.debug("reasoning callback failed", exc_info=True)
            content = delta.get("content")
            if content:
                yield content
