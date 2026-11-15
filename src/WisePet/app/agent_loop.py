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
from dataclasses import dataclass, field
from typing import Callable

from ..domain.errors import AIGatewayError
from .agent_tools import TOOL_SCHEMAS

logger = logging.getLogger(__name__)

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


class AgentRunner:
    def __init__(self, gateway, cfg_provider: Callable[[], object],
                 system_prompt_provider: Callable[[], str], toolbox,
                 max_steps: int = MAX_STEPS):
        self._gateway = gateway
        self._cfg = cfg_provider
        self._system_prompt = system_prompt_provider
        self._toolbox = toolbox
        self._max_steps = max_steps
        #: 网关 key（base_url|model）→ 不支持 tools 协议，避免每次对话都试错
        self._no_tools: set[str] = set()

    # ---------- 入口 ----------

    def run(self, user_text: str, history_messages: list[dict],
            content_callback: Callable[[str], None] | None = None) -> AgentOutcome:
        """content_callback：真流式增量上屏回调；None 则整段返回（旧行为）。"""
        cfg = self._cfg()
        key = f"{cfg.base_url}|{cfg.model}"
        messages = [{"role": "system", "content": self._system_prompt()},
                    *history_messages,
                    {"role": "user", "content": str(user_text or "")}]
        if key in self._no_tools:
            return self._plain_round(messages, degraded=True)
        try:
            return self._loop(messages, cfg, key, content_callback)
        except AIGatewayError as exc:
            if exc.kind != AIGatewayError.PROTOCOL:
                raise                      # 网络/鉴权等照旧上抛走既有错误链
            logger.warning("gateway rejected tools protocol, fallback: %s", exc.detail)
            self._no_tools.add(key)
            return self._plain_round(messages, degraded=True)

    # ---------- 循环 ----------

    def _loop(self, messages: list[dict], cfg, key: str,
              content_callback: Callable[[str], None] | None) -> AgentOutcome:
        tools_used: list[str] = []
        for step in range(1, self._max_steps + 1):
            content, calls = self._chat_step(messages, cfg, content_callback)
            if not calls:
                return AgentOutcome(final_text=str(content or ""),
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
        content, _calls = self._chat_step(messages, cfg, content_callback)
        return AgentOutcome(final_text=str(content or ""), steps=self._max_steps,
                            tools_used=list(tools_used))

    def _chat_step(self, messages: list[dict], cfg,
                   content_callback: Callable[[str], None] | None
                   ) -> tuple[str, list[dict]]:
        """单步模型调用：有回调且网关支持时走流式（增量实时上屏），
        流式被网关拒绝（PROTOCOL）则单次回落非流式，不影响本轮结果。"""
        if content_callback is None:
            return self._gateway.chat_with_tools(messages, cfg, TOOL_SCHEMAS)
        stream = getattr(self._gateway, "chat_with_tools_stream", None)
        if stream is None:
            return self._gateway.chat_with_tools(messages, cfg, TOOL_SCHEMAS)
        try:
            return stream(messages, cfg, TOOL_SCHEMAS,
                          on_content=content_callback)
        except AIGatewayError as exc:
            if exc.kind != AIGatewayError.PROTOCOL:
                raise
            logger.info("stream+tools rejected by gateway (%s), fallback non-stream",
                        exc.detail)
            return self._gateway.chat_with_tools(messages, cfg, TOOL_SCHEMAS)

    def _plain_round(self, messages: list[dict], degraded: bool) -> AgentOutcome:
        """降级：不带工具的流式请求拼成整段（复用既有纯文本协议）。"""
        parts: list[str] = []
        for chunk in self._gateway.stream_chat(messages, self._cfg()):
            parts.append(chunk)
        return AgentOutcome(final_text="".join(parts), steps=1, degraded=degraded)
