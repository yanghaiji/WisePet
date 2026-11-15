"""SummaryMemory：会话摘要——被 max_turns 截掉的旧对话，压缩成一段「还记得」。

借鉴 LangChain ConversationSummaryMemory 的模式，自研实现：
- 每轮对话 observe 进缓冲区，攒满 K 条后由后台线程调一次 AI 压缩成摘要；
- 摘要与旧摘要合并（一次调用完成），落 summary.json 跨重启有效；
- ChatService 组装 messages 时把摘要作为第二条 system 注入——
  聊多久都不会「忘了开头说过什么」。

隐私：摘要只存本机；未配 AI 时功能静默关闭（无摘要可生成）。
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from ..domain.chat_record import day_key
from ..infra.json_store import atomic_write, load_json_or_backup_reset

logger = logging.getLogger(__name__)

FILE_NAME = "summary.json"
#: 缓冲多少条消息后触发一次摘要压缩
DEFAULT_BUFFER_LIMIT = 8
#: 摘要正文上限（提示词里同时约束模型）
MAX_SUMMARY_CHARS = 600
#: 单条消息压缩前截断，防超长粘贴把摘要请求撑爆
MAX_MSG_CHARS = 400

_PROMPT = (
    "你是桌面宠物的记忆模块。请把「旧摘要」和「新增对话」合并成一段简短记忆：\n"
    "保留对后续对话有用的事实（用户偏好、约定、进行中的事、重要决定），"
    "丢掉寒暄与重复；不超过{limit}字，直接输出摘要正文，不要解释。\n\n"
    "旧摘要：\n{old}\n\n新增对话：\n{dialogue}"
)


class SummaryStore:
    def __init__(self, data_dir: Path | str):
        self._path = Path(data_dir) / FILE_NAME

    def load(self) -> dict:
        data = load_json_or_backup_reset(self._path) or {}
        if not isinstance(data, dict):
            return {}
        return {"day": str(data.get("day") or ""),
                "text": str(data.get("text") or "")}

    def save(self, text: str) -> None:
        try:
            atomic_write(self._path, {"day": day_key(), "text": text[:MAX_SUMMARY_CHARS]})
        except OSError:
            logger.exception("summary save failed")

    def clear(self) -> None:
        try:
            if self._path.exists():
                self._path.unlink()
        except OSError:
            logger.exception("summary clear failed")


class SummaryMemory:
    def __init__(self, store: SummaryStore, gateway, cfg_provider,
                 enabled_provider, clock=None,
                 buffer_limit: int = DEFAULT_BUFFER_LIMIT):
        self._store = store
        self._gateway = gateway
        self._cfg = cfg_provider
        self._enabled = enabled_provider
        self._clock = clock
        self._buffer_limit = max(2, int(buffer_limit))
        self._buffer: list[tuple[str, str]] = []
        self._summary = store.load().get("text") or ""
        self._inflight = False
        self._lock = threading.Lock()

    # ---------- 观察 ----------

    def observe(self, role: str, content: str) -> None:
        """每轮对话喂进来；攒满触发异步摘要（绝不阻塞回复）。"""
        if not self._enabled():
            return
        text = str(content or "").strip()[:MAX_MSG_CHARS]
        if not text:
            return
        with self._lock:
            self._buffer.append((str(role), text))
            enough = len(self._buffer) >= self._buffer_limit
        if enough and not self._inflight:
            self._summarize_async()

    def reconcile(self, day_records, session_turns) -> None:
        """重启回灌后，把「当天更早、已被会话截断」的消息补进缓冲区。

        day_records：按时间的当日聊天记录（含 user/assistant 与内容）；
        session_turns：ChatSession 现存的（role, content）序列。
        """
        if not self._enabled():
            return
        kept = {(str(r), str(c)) for r, c in session_turns}
        for record in day_records:
            role = getattr(record, "role", "")
            content = str(getattr(record, "content", "") or "")[:MAX_MSG_CHARS]
            if role in ("user", "assistant") and content \
                    and (role, content) not in kept:
                with self._lock:
                    self._buffer.append((role, content))

    def prefix_line(self) -> str | None:
        """注入 messages 的摘要前缀；没有摘要返回 None（不污染提示词）。"""
        if not self._enabled() or not self._summary:
            return None
        return f"\n早些时候的对话摘要（可能来自前几天，供你参考，不必复述）：{self._summary}"

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()
        self._summary = ""
        self._store.clear()

    # ---------- 摘要生成 ----------

    def _summarize_async(self) -> None:
        self._inflight = True

        def _work():
            try:
                self._summarize()
            except Exception:  # noqa: BLE001 - 摘要是增强，任何失败都静默等下次
                logger.exception("conversation summary failed")
            finally:
                self._inflight = False

        threading.Thread(target=_work, name="wisepet-summary",
                         daemon=True).start()

    def _summarize(self) -> None:
        with self._lock:
            if len(self._buffer) < self._buffer_limit:
                return
            pending = list(self._buffer)
            self._buffer.clear()
        dialogue = "\n".join(f"{'用户' if r == 'user' else '宠物'}：{c}"
                             for r, c in pending)
        prompt = _PROMPT.format(limit=MAX_SUMMARY_CHARS // 2,
                                old=self._summary or "（无）", dialogue=dialogue)
        text = self._gateway.complete([{"role": "user", "content": prompt}],
                                      self._cfg())
        merged = str(text or "").strip()[:MAX_SUMMARY_CHARS]
        if merged:
            self._summary = merged
            self._store.save(merged)
            logger.info("conversation summary updated (%d chars)", len(merged))

    # ---------- 调试 ----------

    def state(self) -> dict:
        with self._lock:
            return {"summary_chars": len(self._summary),
                    "buffered": len(self._buffer), "day": day_key()}
