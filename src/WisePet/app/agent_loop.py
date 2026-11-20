"""AgentRunner：function calling 的多步循环——LangGraph 式编排的自研实现。

一轮对话可能需要多步工具：先读资料、再查历史、最后回答或发起写盘动作。
本模块把「模型请求工具 → 本地执行 → 结果回喂 → 继续」的循环封起来：

- 只读工具在循环内直接执行，结果以 role=tool 回喂；
- 写盘 / 排期动作不在线程内执行，作为 pending_action 交回组装根走
  既有的三级权限 + 预览确认 + 审计 + 撤销链路（human-in-the-loop 中断点）；
- 超过步数上限后强制一次无工具总结，避免无限循环；
- 网关不支持 tools 协议时自动降级为纯文本对话（按 base_url|model 记忆），
  并回落到现有 JSON-in-prompt 协议——老的兼容路径一条都不丢。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Callable

from ..domain.errors import AIGatewayError
from .agent_tools import TOOL_SCHEMAS

logger = logging.getLogger(__name__)

#: 画图意图正则：覆盖「画一个/画张/画只/帮我画/生成张图/证件照」等口语说法。
#: 只用于关键词兜底——模型没调工具时，用户明确要图仍要真的画。
_IMAGE_INTENT_RE = re.compile(
    r"画\s*[一个张只幅副条]"
    r"|再画|也画|帮我画|给我画|来张图|来幅"
    r"|生成.{0,6}(?:图片|图|照片|插画|头像)"
    r"|画.{0,10}(?:证件照|头像|插画|动漫|卡通|画)"
)


def wants_image(text: str) -> bool:
    """用户这句话是否明确要一张新图（纯函数，供兜底与测试用）。"""
    return bool(_IMAGE_INTENT_RE.search(str(text or "")))


_VIDEO_INTENT_RE = re.compile(
    # 动词 + 0~16 字修饰 + 视频/动画名词（覆盖「生成一个可爱的小猫跳舞的视频
    # 10s左右的」这类长定语，真机就漏在这）。名词在后、动词在前的说法也认。
    r"(?:生成|做|拍|来|要|整|录).{0,16}(?:视频|动画|短片)"
    r"|(?:视频|动画|短片).{0,8}(?:生成|做|来一段|安排|搞一个)"
    r"|来一段|拍一段")


def wants_video(text: str) -> bool:
    """用户这句话是否明确要一段新视频（纯函数，供兜底与测试用）。"""
    return bool(_VIDEO_INTENT_RE.search(str(text or "")))


#: 工具循环步数上限：防失控循环，也防止 token 失控
MAX_STEPS = 6
#: 单条工具结果回喂给模型的字符上限（工具输出可能很长）
MAX_TOOL_RESULT_CHARS = 6000


@dataclass
class AgentOutcome:
    """一轮 agent 对话的结果。

    final_text  = 给用户看的最终回答（可为空，比如只发起了写盘动作）；
    pending_action = 需要主线程确认/执行的写盘或排期动作；
    tools_used  = 实际执行过的工具名（供日志与埋点）；
    degraded    = True 表示本次网关不支持 tools，已回落纯文本协议。
    """

    final_text: str = ""
    pending_action: object | None = None
    steps: int = 0
    tools_used: list[str] = field(default_factory=list)
    degraded: bool = False
    images: tuple[str, ...] = ()   # 本轮生成的本地图片路径（工具产出）
    videos: tuple[str, ...] = ()   # 本轮生成的本地视频路径（工具产出）


class AgentRunner:
    def __init__(self, gateway, cfg_provider: Callable[[], object],
                 system_prompt_provider: Callable[[], str], toolbox,
                 max_steps: int = MAX_STEPS,
                 keyword_fallbacks: tuple[tuple[tuple[str, ...], str], ...] = ()):
        self._gateway = gateway
        self._cfg = cfg_provider
        self._system_prompt = system_prompt_provider
        self._toolbox = toolbox
        self._max_steps = max_steps
        #: 关键词兜底：(触发词, 工具名)。模型没按提示调用工具时，用户明确要求的事
        #: 仍要做（如「画一张」必须真画图，不能只回一句「画好了」）。
        self._keyword_fallbacks = keyword_fallbacks
        #: 网关 key（base_url|model）→ 不支持 tools 协议，避免每次对话都试错
        self._no_tools: set[str] = set()

    # ---------- 入口 ----------

    def run(self, user_text: str, history_messages: list[dict],
            content_callback: Callable[[str], None] | None = None,
            reasoning_callback: Callable[[str], None] | None = None) -> AgentOutcome:
        """content_callback：真流式增量上屏回调；None 则整段返回（旧行为）。
        reasoning_callback：推理模型的思考过程增量（与正文分开）。"""
        # 每轮开始清空上轮的工具产出（图片路径），避免串到下一轮对话
        generated = getattr(self._toolbox, "generated_images", None)
        videos = getattr(self._toolbox, "generated_videos", None)
        if isinstance(generated, list):
            generated.clear()
        if isinstance(videos, list):
            videos.clear()
        outcome = self._run_inner(user_text, history_messages, content_callback,
                                  reasoning_callback)
        self._apply_keyword_fallbacks(user_text, outcome)
        if isinstance(generated, list) and generated:
            outcome.images = tuple(generated)
        if isinstance(videos, list) and videos:
            outcome.videos = tuple(videos)
        return outcome

    def _apply_keyword_fallbacks(self, user_text: str, outcome: AgentOutcome) -> None:
        """模型没调用但用户明确要求的工具，这里补做一次（不覆盖已有 results）。"""
        if not self._keyword_fallbacks:
            return
        text = str(user_text or "")
        for matcher, tool_name in self._keyword_fallbacks:
            if tool_name in outcome.tools_used:
                continue
            hit = matcher(text) if callable(matcher) else any(kw in text for kw in matcher)
            if not hit:
                continue
            try:
                result, _action = self._toolbox.execute(tool_name, {"prompt": text})
            except Exception:  # noqa: BLE001 - 兜底失败不影响已生成的回复
                logger.debug("keyword fallback %s failed", tool_name, exc_info=True)
                continue
            outcome.tools_used.append(tool_name)
            # 把工具的真实结果告诉用户：
            # - 成功：带上本地路径，别只说「画好了」却看不到东西
            # - 失败：必须覆盖模型那句「好嘞」（否则用户以为成了，干等），
            #   明确说出失败原因（未启用/没配置/API 拒绝等）
            failed = any(mark in result for mark in
                         ("失败", "未启用", "没配置", "未配置", "没做成"))
            if failed:
                outcome.final_text = result
            elif result:
                base = outcome.final_text or ""
                outcome.final_text = (base + "\n" if base else "") + result
            break

    def _run_inner(self, user_text: str, history_messages: list[dict],
                   content_callback: Callable[[str], None] | None,
                   reasoning_callback: Callable[[str], None] | None = None) -> AgentOutcome:
        cfg = self._cfg()
        key = f"{cfg.base_url}|{cfg.model}"
        messages = [{"role": "system", "content": self._system_prompt()},
                    *history_messages,
                    {"role": "user", "content": str(user_text or "")}]
        if key in self._no_tools:
            return self._plain_round(messages, degraded=True)
        try:
            return self._loop(messages, cfg, key, content_callback, reasoning_callback)
        except AIGatewayError as exc:
            if exc.kind != AIGatewayError.PROTOCOL:
                raise                      # 网络/鉴权等照旧上抛走既有错误链
            logger.warning("gateway rejected tools protocol, fallback: %s", exc.detail)
            self._no_tools.add(key)
            return self._plain_round(messages, degraded=True,
                                     reasoning_callback=reasoning_callback)

    # ---------- 循环 ----------

    def _loop(self, messages: list[dict], cfg, key: str,
              content_callback: Callable[[str], None] | None,
              reasoning_callback: Callable[[str], None] | None = None) -> AgentOutcome:
        tools_used: list[str] = []
        weather_card: str = ""      # get_weather 返回的 markdown 卡片，用于格式兜底
        for step in range(1, self._max_steps + 1):
            content, calls = self._chat_step(messages, cfg, content_callback,
                                              reasoning_callback)
            if not calls:
                final = self._final_with_weather_card(content, weather_card)
                return AgentOutcome(final_text=final,
                                    steps=step, tools_used=list(tools_used))
            messages.append({"role": "assistant", "content": content or "",
                             "tool_calls": [
                                 {"id": c["id"], "type": "function",
                                  "function": {"name": c["name"],
                                               "arguments": json.dumps(c["arguments"],
                                                                       ensure_ascii=False)}}
                                 for c in calls]})
            for call in calls:
                result, action = self._toolbox.execute(call["name"], call["arguments"])
                tools_used.append(call["name"])
                if call["name"] == "get_weather" and str(result).lstrip().startswith("### "):
                    weather_card = str(result)
                if action is not None:
                    # 写盘/排期：交回主线程走权限确认链路，工具结果不再回喂
                    logger.info("agent step %d yields pending action: %s", step,
                                call["name"])
                    return AgentOutcome(final_text=result, pending_action=action,
                                        steps=step, tools_used=list(tools_used))
                messages.append({"role": "tool", "tool_call_id": call["id"],
                                 "content": result[:MAX_TOOL_RESULT_CHARS]})
        # 步数耗尽：不带工具再做一次总结，把已有工具结果收敛成回答
        logger.info("agent max steps reached (%d), forcing summary", self._max_steps)
        content, _calls = self._chat_step(messages, cfg, content_callback,
                                           reasoning_callback)
        final = self._final_with_weather_card(content, weather_card)
        return AgentOutcome(final_text=final, steps=self._max_steps,
                            tools_used=list(tools_used))

    @staticmethod
    def _final_with_weather_card(content, card: str) -> str:
        """弱模型把天气卡片揉成一段话时兜底恢复 markdown 格式（FR v1.35）。"""
        if not card:
            return str(content or "")
        from ..domain.weather import ensure_card_format
        return ensure_card_format(str(content or ""), card)

    def _chat_step(self, messages: list[dict], cfg,
                   content_callback: Callable[[str], None] | None,
                   reasoning_callback: Callable[[str], None] | None = None
                   ) -> tuple[str, list[dict]]:
        """单步模型调用：有回调且网关支持时走流式（增量实时上屏），
        流式被网关拒绝（PROTOCOL）则单次回落非流式，不影响本轮结果。"""
        if content_callback is None:
            return self._gateway.chat_with_tools(
                messages, cfg, TOOL_SCHEMAS, on_reasoning=reasoning_callback)
        stream = getattr(self._gateway, "chat_with_tools_stream", None)
        if stream is None:
            return self._gateway.chat_with_tools(
                messages, cfg, TOOL_SCHEMAS, on_reasoning=reasoning_callback)
        try:
            return stream(messages, cfg, TOOL_SCHEMAS,
                          on_content=content_callback,
                          on_reasoning=reasoning_callback)
        except AIGatewayError as exc:
            if exc.kind != AIGatewayError.PROTOCOL:
                raise
            logger.info("stream+tools rejected by gateway (%s), fallback non-stream",
                        exc.detail)
            return self._gateway.chat_with_tools(
                messages, cfg, TOOL_SCHEMAS, on_reasoning=reasoning_callback)

    def _plain_round(self, messages: list[dict], degraded: bool,
                     reasoning_callback: Callable[[str], None] | None = None) -> AgentOutcome:
        """降级：不带工具的流式请求拼成整段（复用既有纯文本协议）。"""
        parts: list[str] = []
        for chunk in self._gateway.stream_chat(
                messages, self._cfg(), on_reasoning=reasoning_callback):
            parts.append(chunk)
        return AgentOutcome(final_text="".join(parts), steps=1, degraded=degraded)
