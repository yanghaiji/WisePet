"""KnowledgeBase：让宠物「读过你的资料」。

用户显式指定一个本机文件夹 → 切块建倒排索引（BM25 近似打分，纯标准库、
jieba 缺失时 bigram 降级）→ 聊天时按当前问题检索片段注入系统提示词。

隐私边界：默认不启用、只索引白名单文本类型、只存在本机 knowledge.json、
不执行文件、不上传、不跟随符号链接、总量与单文件都有上限。
"""
from __future__ import annotations

import logging
import math
import re
import time
from pathlib import Path

from .nlp import bigram_split

logger = logging.getLogger(__name__)

#: 只索引纯文本类文件
ALLOWED_SUFFIXES = frozenset({
    ".txt", ".md", ".markdown", ".py", ".json", ".csv", ".log",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".rst", ".js", ".ts",
    ".html", ".css", ".sql", ".sh", ".bat", ".ps1",
})
MAX_FILE_BYTES = 512 * 1024
MAX_FILES = 300
MAX_TOTAL_BYTES = 20 * 1024 * 1024
CHUNK_CHARS = 800
CHUNK_OVERLAP = 100
MAX_CHUNKS = 2000
#: 开启语义检索时参与向量化的 chunk 上限（控制 knowledge.json 体积）
MAX_VECTOR_CHUNKS = 500
EMBED_BATCH = 32
#: 混合打分里语义余弦的权重（BM25 已归一化到 0~1）
SEMANTIC_WEIGHT = 1.2
MAX_PROMPT_CHARS = 3200
#: 注入片段默认条数（v1.32 放大召回，细节交给模型与 agent 工具）
DEFAULT_TOP_K = 6
EXCERPT_CHARS = 800
_WORD_RE = re.compile(r"[a-z0-9_]{2,}")


def _terms(text: str) -> list[str]:
    """中英混排分词：英文/数字按词，中文用 bigram（jieba 缺失时同样可用）。"""
    lowered = str(text or "").lower()
    words = _WORD_RE.findall(lowered)
    cjk = re.sub(r"[a-z0-9_\s]", "", lowered)
    return words + bigram_split(cjk)


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (na * nb)


def _chunks(text: str) -> list[str]:
    step = max(1, CHUNK_CHARS - CHUNK_OVERLAP)
    return [text[i:i + CHUNK_CHARS] for i in range(0, len(text), step)]


class KnowledgeBase:
    def __init__(self, store, embedding_fn=None):
        """embedding_fn：texts -> list[list[float]]；None = 纯关键词检索。"""
        self._store = store
        self._index = store.load()
        self._embedding_fn = embedding_fn

    # ---------- 状态 ----------

    def status(self) -> dict:
        chunks = self._index.get("chunks") or []
        return {
            "folder": self._index.get("folder", ""),
            "built_at": float(self._index.get("built_at") or 0.0),
            "files": int(self._index.get("files") or 0),
            "chunks": len(chunks),
            "vectors": sum(1 for c in chunks if c.get("vec")),
            "semantic": bool(self._embedding_fn),
        }

    # ---------- 建索引 ----------

    def build(self, folder: str, now: float | None = None) -> dict:
        """重建索引；返回统计。任何异常都抛出由调用方转成用户可见提示。"""
        root = Path(str(folder or "")).expanduser()
        if not str(folder or "").strip() or not root.is_dir():
            raise ValueError("资料文件夹不存在或不是目录")
        files, total = [], 0
        for path in sorted(root.rglob("*")):
            if len(files) >= MAX_FILES or total >= MAX_TOTAL_BYTES:
                break
            if not path.is_file() or path.is_symlink():
                continue
            if path.suffix.lower() not in ALLOWED_SUFFIXES:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > MAX_FILE_BYTES:
                continue
            files.append(path)
            total += size

        chunks: list[dict] = []
        for path in files:
            if len(chunks) >= MAX_CHUNKS:
                break
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for piece in _chunks(text):
                if len(chunks) >= MAX_CHUNKS:
                    break
                if not piece.strip():
                    continue
                tf: dict[str, int] = {}
                for term in _terms(piece):
                    tf[term] = tf.get(term, 0) + 1
                chunks.append({"path": str(path), "text": piece, "tf": tf})

        self._index = {"folder": str(root),
                       "built_at": float(time.time() if now is None else now),
                       "files": len(files), "chunks": chunks}
        self._embed_chunks(chunks)
        self._store.save(self._index)
        logger.info("knowledge index built: files=%d chunks=%d", len(files), len(chunks))
        return {"files": len(files), "chunks": len(chunks), "folder": str(root)}

    def _embed_chunks(self, chunks: list[dict]) -> None:
        """给 chunk 生成语义向量（可降级：没有 embedding_fn 或失败则跳过）。"""
        if self._embedding_fn is None or not chunks:
            return
        targets = chunks[:MAX_VECTOR_CHUNKS]
        texts = [c["text"] for c in targets]
        try:
            vectors = self._embedding_fn(texts)
        except Exception as exc:  # noqa: BLE001 - 语义是增强，失败回落纯关键词
            logger.warning("embedding failed, keep keyword-only index: %s", exc)
            return
        if len(vectors) != len(texts):
            logger.warning("embedding count mismatch (%s/%s), skip vectors",
                           len(vectors), len(texts))
            return
        for chunk, vec in zip(targets, vectors):
            chunk["vec"] = [round(float(x), 5) for x in vec]
        logger.info("embedded %d chunks", len(targets))

    def clear(self) -> None:
        self._index = {}
        self._store.clear()

    # ---------- 检索 ----------

    def search(self, query: str, top_k: int = 3) -> list[dict]:
        chunks = self._index.get("chunks") or []
        if not chunks or not str(query or "").strip():
            return []
        terms = list(dict.fromkeys(_terms(query)))[:24]
        if not terms:
            return []
        total = len(chunks)
        df: dict[str, int] = {}
        for term in terms:
            df[term] = sum(1 for c in chunks if term in (c.get("tf") or {}))
        query_vec = self._query_vector(query)
        scored = []
        for chunk in chunks:
            tf = chunk.get("tf") or {}
            length = max(1.0, math.sqrt(len(chunk.get("text") or "")))
            score = 0.0
            for term in terms:
                if tf.get(term):
                    idf = math.log(1.0 + (total - df[term] + 0.5) / (df[term] + 0.5))
                    score += (1.0 + math.log(tf[term])) * idf
            score /= length
            if query_vec is not None and chunk.get("vec"):
                score += SEMANTIC_WEIGHT * max(0.0, _cosine(query_vec, chunk["vec"]))
            if score > 0:
                scored.append((score, chunk))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [{"path": c.get("path", ""), "text": c.get("text", ""),
                 "score": round(s, 4)} for s, c in scored[:max(1, int(top_k))]]

    def _query_vector(self, query: str) -> list[float] | None:
        """查询向量：没有 embedding_fn 或调用失败返回 None（降级纯 BM25）。"""
        if self._embedding_fn is None:
            return None
        try:
            vectors = self._embedding_fn([query])
        except Exception as exc:  # noqa: BLE001 - 语义是增强，失败降级
            logger.warning("query embedding failed, fallback to BM25: %s", exc)
            return None
        return vectors[0] if vectors else None

    def prompt_context(self, query: str, top_k: int = DEFAULT_TOP_K) -> str:
        """注入系统提示词的片段；没有命中返回空串（不污染提示词）。"""
        hits = self.search(query, top_k=top_k)
        if not hits:
            return ""
        lines = ["\n以下是你读过的本机资料片段（只在相关时引用，别硬塞；"
                 "需要更多细节可以用 search_knowledge / read_file 工具继续查）："]
        used = 0
        for hit in hits:
            excerpt = re.sub(r"\s+", " ", hit["text"]).strip()[:EXCERPT_CHARS]
            block = f"- 来自 {Path(hit['path']).name}：{excerpt}"
            if used + len(block) > MAX_PROMPT_CHARS:
                break
            lines.append(block)
            used += len(block)
        return "\n".join(lines)
