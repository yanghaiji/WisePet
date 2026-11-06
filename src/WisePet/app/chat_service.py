"""ChatService：会话编排、人设组装、串行队列与错误映射（PRD M4）。"""
from __future__ import annotations

import logging
import time
from collections import deque
from typing import Callable

from ..domain.attachment import build_text_context
from ..domain.chat_record import day_key
from ..domain.chat_session import ChatSession
from ..domain.errors import AIGatewayError
from ..domain.events import (
    PHASE_DONE,
    PHASE_ERROR,
    PHASE_REPLYING,
    PHASE_THINKING,
    ChatErrorEvent,
    ChatReplyChunkEvent,
    ChatStateChangedEvent,
    ChatTurnRecordedEvent,
)
from ..domain.ports import AIChatGateway, AIConfig
from .local_responder import LocalResponder

logger = logging.getLogger(__name__)

#: 本地模式拟流式：每攒够这么多字符（或遇到断句标点）就投一帧
LOCAL_CHUNK_CHARS = 8
LOCAL_CHUNK_BREAKS = "，。！？…；：、\n"

#: 回复正文在日志里只留这么多字（气泡里是全的，日志只求能认出“是哪一条”）
REPLY_PREVIEW_CHARS = 60


def _preview(text: str) -> str:
    """日志预览：截断并压掉换行，保证一条回复只占一行日志。"""
    return str(text or "")[:REPLY_PREVIEW_CHARS].replace("\n", "↘")


def _multimodal_message(text: str, attachments) -> dict:
    """OpenAI 兼容多模态用户消息：文本段 + 图片段（文本附件已拼进正文）。"""
    parts: list[dict] = [{"type": "text",
                          "text": build_text_context(attachments, text)}]
    for att in attachments:
        if att.kind == "image":
            parts.append({"type": "image_url",
                          "image_url": {"url": att.data_uri}})
    return {"role": "user", "content": parts}


def stream_pieces(text: str) -> list[str]:
    """把整句回复切成拟流式小段，让气泡有逐字出现的感觉。"""
    pieces: list[str] = []
    buf = ""
    for char in text:
        buf += char
        if char in LOCAL_CHUNK_BREAKS or len(buf) >= LOCAL_CHUNK_CHARS:
            pieces.append(buf)
            buf = ""
    if buf:
        pieces.append(buf)
    return pieces


#: 错误 → 用户可读兜底话术（PRD US-3 验收）
FALLBACK_TEXTS = {
    AIGatewayError.AUTH: "我还没连上大脑哦，检查下 AI 配置好么？",
    AIGatewayError.NETWORK: "网络好像不通…我连不上啦",
    AIGatewayError.RATE: "主人太热情啦，我喘口气稍后再说…",
    AIGatewayError.TIMEOUT: "大脑死机了…请求超时啦",
    AIGatewayError.SERVER: "AI 服务开了小差，等会再试试吧",
    # 连上了但读不出内容：几乎都是 Base URL 填成了其他协议的地址
    AIGatewayError.PROTOCOL: "连上了，但它回的话我读不懂——Base URL 请填 OpenAI 兼容地址（一般以 /v1 或 /v4 结尾）",
}
UNKNOWN_FALLBACK = "呜呜，我一时说不出话来…"


class ChatService:
    """消息串行队列：send 入队，process_next 由 worker 线程逐条消费。"""

    def __init__(self, gateway: AIChatGateway, session: ChatSession, bus,
                 config_provider: Callable[[], AIConfig],
                 system_prompt_provider: Callable[[], str],
                 local_responder: LocalResponder | None = None):
        self._gateway = gateway
        self._session = session
        self._bus = bus
        self._config_provider = config_provider
        self._system_prompt_provider = system_prompt_provider
        self._local = local_responder
        self._queue: deque[str] = deque()
        self._cancelled = False
        #: 只为日志能把「一次请求」与「一条回复」串起来，不代表业务语义
        self._seq = 0

    # ---------- 命令 ----------

    def send(self, text: str, attachments=()) -> bool:
        """入队一条用户消息（可带附件）；空白消息且无附件被拒绝（不变量 C2）。

        同时发布「用户这一轮」与「开始思考」两个事实：聊天窗据此上屏与显示状态，
        按天仓储据此落盘。历史事件不复用流式 chunk，避免增量拼出半截记录。
        附件只影响本次请求：图片转多模态段，文本文件拼进正文；会话上下文仍只存
        纯文本（图片不驻留历史，避免上下文体积膨胀）。
        """
        stripped = str(text or "").strip()
        attachments = tuple(attachments or ())
        if not stripped and not attachments:
            return False
        self._session.add_turn("user", stripped or "[附件]")
        self._queue.append((stripped, attachments))
        ts = time.time()
        sid = day_key(ts)
        self._bus.publish(ChatTurnRecordedEvent(role="user", content=stripped,
                                                source="user", ts=ts, session_id=sid))
        self._bus.publish(ChatStateChangedEvent(phase=PHASE_THINKING,
                                                source=self._brain_source(),
                                                session_id=sid))
        return True

    def _brain_source(self) -> str:
        """当前这条由谁作答：与 process_next 的路由判据保持同一口径。"""
        cfg = self._config_provider()
        return "local" if (self._local is not None and not cfg.configured) else "ai"

    def has_pending(self) -> bool:
        return bool(self._queue)

    def cancel_inflight(self) -> None:
        self._cancelled = True

    # ---------- 会话状态与记录（事件供聊天窗 / 按天仓储消费） ----------

    def _publish_phase(self, phase: str, source: str = "", detail: str = "") -> None:
        self._bus.publish(ChatStateChangedEvent(phase=phase, source=source,
                                                detail=detail, session_id=day_key()))

    def _publish_turn(self, role: str, content: str, source: str) -> None:
        ts = time.time()
        self._bus.publish(ChatTurnRecordedEvent(role=role, content=content, source=source,
                                                ts=ts, session_id=day_key(ts)))

    # ---------- 消费（worker 线程调用） ----------

    def process_next(self) -> bool:
        """处理队列头部一条消息；返回 False 表示队列已空。"""
        if not self._queue:
            return False
        text, attachments = self._queue.popleft()
        self._cancelled = False
        self._seq += 1
        seq = self._seq
        cfg = self._config_provider()
        source = self._brain_source()
        # send 时已发过一次 THINKING，这里按实际执行时的配置校正来源（热改配置后不失真）
        self._publish_phase(PHASE_THINKING, source)
        if source == "local":
            self._reply_locally(text, seq, source)
            return True
        messages = self._session.build_messages(self._system_prompt_provider())
        if attachments:
            messages[-1] = _multimodal_message(text, attachments)
        prompt_chars = sum(len(str(m.get("content") or "")) for m in messages)
        # 用户要能分辨“这条是模型答的还是小脑答的”，所以每条请求都先落一行来源日志
        logger.info("chat #%d source=ai request model=%s endpoint=%s msgs=%d prompt_chars=%d",
                    seq, cfg.model, cfg.chat_endpoint, len(messages), prompt_chars)
        started = time.monotonic()
        parts: list[str] = []
        first_wait = None  # 首段上屏等了多久：“半天不出字”与“出字慢”靠它区分
        try:
            for chunk in self._gateway.stream_chat(messages, cfg):
                if self._cancelled:
                    logger.info("chat #%d source=ai cancelled by user", seq)
                    break
                if first_wait is None:
                    first_wait = time.monotonic() - started
                    self._publish_phase(PHASE_REPLYING, source)
                parts.append(chunk)
                self._bus.publish(ChatReplyChunkEvent(text_delta=chunk))
            reply = "".join(parts)
            if reply:
                self._session.add_turn("assistant", reply)
                self._publish_turn("assistant", reply, source)
            self._bus.publish(ChatReplyChunkEvent(text_delta="", done=True, reply=reply))
            self._publish_phase(PHASE_DONE, source)
            logger.info(
                "chat #%d source=ai done chunks=%d reply_chars=%d first_chunk=%s "
                "elapsed=%.2fs%s preview=%s",
                seq, len(parts), len(reply),
                "n/a" if first_wait is None else f"{first_wait:.2f}s",
                time.monotonic() - started,
                " cancelled=True" if self._cancelled else "", _preview(reply))
        except AIGatewayError as exc:
            logger.warning("chat #%d source=ai failed [%s]: %s", seq, exc.kind, exc.detail)
            self._bus.publish(ChatErrorEvent(kind=exc.kind, detail=exc.detail))
            self._on_failure(exc.kind, source)
        except Exception:  # noqa: BLE001 - 兜底未知异常，保证不崩（PRD NFR 可靠）
            logger.exception("chat #%d source=ai unexpected failure", seq)
            kind = AIGatewayError.SERVER
            self._bus.publish(ChatErrorEvent(kind=kind, detail="unexpected"))
            self._on_failure(kind, source)
        return True

    def _on_failure(self, kind: str, source: str) -> None:
        """失败也要有终态：聊天窗显示错误类别，并把兜底话术记成一条助手回复。"""
        self._publish_phase(PHASE_ERROR, source, detail=kind)
        self._publish_turn("assistant", self.fallback_text(kind), "fallback")

    def _reply_locally(self, text: str, seq: int, source: str = "local") -> None:
        """未配 AI Key 时的本地模式：规则回复 + 拟流式投递，不当成错误处理。

        本地回复是纯计算、瞬间完成，因此不检查 _cancelled（避免漏发 done 事件
        让 ui 永远停在流式状态）。
        """
        started = time.monotonic()
        reply = self._local.reply(text)
        for index, piece in enumerate(stream_pieces(reply)):
            if index == 0:
                self._publish_phase(PHASE_REPLYING, source)
            self._bus.publish(ChatReplyChunkEvent(text_delta=piece))
        self._session.add_turn("assistant", reply)
        self._publish_turn("assistant", reply, source)
        self._bus.publish(ChatReplyChunkEvent(text_delta="", done=True, reply=reply))
        self._publish_phase(PHASE_DONE, source)
        logger.info("chat #%d source=local intent=%s reply_chars=%d elapsed=%.2fs preview=%s",
                    seq, self._local.last_intent, len(reply), time.monotonic() - started,
                    _preview(reply))

    @staticmethod
    def fallback_text(kind: str) -> str:
        return FALLBACK_TEXTS.get(kind, UNKNOWN_FALLBACK)
