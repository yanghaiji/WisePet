"""统一记忆检索：一个问题同时查所有记忆源，按相关度/时效排序返回。

之前 AI 要分别调 search_history / recall_memories / search_knowledge 三个工具，
用户说「上次那个项目」时得自己猜该查哪个。这里把它收敛成一个 recall()：

- 长期事实：LongTermMemory.relevant()（关键词重合 × 重要度 × 时效 × 热度）
- 聊天记录：ChatHistory.search()（词元匹配 + 子串兜底）
- 知识库：KnowledgeBase.search()（BM25 + 可选语义）
- 近期处境：SituationService.active()（当前有效的那一条）
- 摘要：SummaryMemory 当前摘要

全部本机、只读、不上传；各源缺失/失败时跳过，单一源异常不影响整体。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: 各源默认召回条数
DEFAULT_LIMIT = 5
#: 单条结果文本上限（回喂模型前截断）
ITEM_CHARS = 200


@dataclass(frozen=True)
class MemoryHit:
    source: str          # fact / chat / knowledge / situation / summary
    text: str
    score: float         # 0~1 归一相关度（各源内部打分不同，这里只做粗排）
    detail: str = ""     # 出处（日期/文件路径）


class UnifiedMemory:
    def __init__(self, *, long_term=None, chat_history=None, knowledge=None,
                 situation=None, summary=None):
        self._long_term = long_term
        self._chat_history = chat_history
        self._knowledge = knowledge
        self._situation = situation
        self._summary = summary

    def recall(self, question: str, limit: int = DEFAULT_LIMIT) -> list[MemoryHit]:
        """按问题检索全部记忆，返回按相关度降序的命中列表。"""
        q = str(question or "").strip()
        if not q:
            return []
        hits: list[MemoryHit] = []
        for collector in (self._facts, self._chats, self._knowledge_hits,
                          self._situations, self._summaries):
            try:
                hits.extend(collector(q))
            except Exception:  # noqa: BLE001 - 单一源失败不影响其它记忆
                logger.debug("memory source failed", exc_info=True)
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:max(1, int(limit))]

    def prompt_context(self, question: str, limit: int = DEFAULT_LIMIT) -> str:
        """把检索结果拼成可注入提示词的一段文本；无命中返回空串。"""
        hits = self.recall(question, limit=limit)
        if not hits:
            return ""
        label = {"fact": "长期记忆", "chat": "历史聊天", "knowledge": "资料",
                 "situation": "近况", "summary": "对话摘要"}
        lines = ["\n以下是按当前问题从本机记忆里检索到的内容"
                 "（只在相关时引用，别硬塞；没有就当没查到）："]
        for hit in hits:
            tag = label.get(hit.source, hit.source)
            where = f"（{hit.detail}）" if hit.detail else ""
            lines.append(f"- {tag}{where}：{hit.text[:ITEM_CHARS]}")
        return "\n".join(lines)

    # ---------- 各源 ----------

    def _facts(self, q: str) -> list[MemoryHit]:
        if self._long_term is None:
            return []
        out = []
        for entry in self._long_term.relevant(q, top_k=5):
            text = f"{entry.get('key')}：{entry.get('value')}"
            out.append(MemoryHit("fact", text, 0.85, str(entry.get("day") or "")))
        return out

    def _chats(self, q: str) -> list[MemoryHit]:
        if self._chat_history is None:
            return []
        out = []
        for record in self._chat_history.search(q, limit=5):
            day = str(getattr(record, "day", "") or "")
            out.append(MemoryHit("chat", str(record.content or ""), 0.7, day))
        return out

    def _knowledge_hits(self, q: str) -> list[MemoryHit]:
        if self._knowledge is None:
            return []
        out = []
        for hit in self._knowledge.search(q, top_k=3):
            out.append(MemoryHit("knowledge", str(hit.get("text") or ""),
                                 0.65, str(hit.get("path") or "")))
        return out

    def _situations(self, q: str) -> list[MemoryHit]:
        if self._situation is None:
            return []
        from .nlp import Tokenizer, overlap_score
        tokenizer = Tokenizer()
        q_grams = tokenizer.grams(q)
        text = self._situation.prompt_line()
        if not text or not q_grams:
            return []
        score = overlap_score(q_grams, tokenizer.grams(text))
        return [MemoryHit("situation", text.strip(), 0.6 + score * 0.3)] \
            if score > 0 else []

    def _summaries(self, q: str) -> list[MemoryHit]:
        if self._summary is None:
            return []
        text = self._summary.prefix_line()
        return [MemoryHit("summary", text.strip(), 0.5)] if text else []
