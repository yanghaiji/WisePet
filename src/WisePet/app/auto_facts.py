"""AI 驱动的通用事实记忆：从对话里抽取任意值得长期记住的用户事实。

为什么不用正则：固定句式只能接住「我叫X」，用户换个说法/换个话题（喜好、
住址、项目、约定、宠物…）就漏记。这里把「什么是事实」交给模型判断，
程序只负责：攒对话 → 一次 AI 调用出 JSON → 去重 → 落本机 fact_entries。

- 复用既有 `LongTermMemory.remember()` 的落盘 / TTL / 时间线 / 提示词注入；
- 后台线程执行，绝不阻塞回复；失败静默等下次（记忆是增强不是主链路）；
- 短陈述线索触发「立即抽取」（只决定是否触发，不解析内容），保证用户
  说完一句就退出也来得及记住；
- 全程本机；未配 AI 或关掉长期记忆时整体停用。
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Callable

from ..domain.situation import SITUATION_KINDS

logger = logging.getLogger(__name__)

BUFFER_LIMIT = 8
MAX_FACTS_PER_CALL = 6

#: 只作为「立即触发 AI 抽取」的线索，不用于解析事实内容（换说法照样由 AI 判断）
#: 情绪吐槽也列入，否则只说一句「累死了」就退出时来不及识别。
_STATEMENT_CLUES = (
    "我叫", "我是", "我的", "我姓", "我住", "我在", "我有", "我喜欢", "我讨厌",
    "我不", "我养", "我最近", "我平时", "我打算", "我准备", "记住", "别忘了",
    "以后叫我", "叫我", "我从", "我会", "我最", "我家",
    "好累", "太累", "累死", "好烦", "烦死", "气死", "崩溃", "压力大",
    "受不了", "绷不住", "焦虑", "糟心",
)
#: 问句不触发立即抽取（问「我叫什么」不是陈述）
_QUESTION_MARKS = ("什么", "吗", "？", "?", "几", "哪", "谁", "怎么")

_PROMPT = (
    "你是桌面宠物的记忆模块。请从下面的对话里输出两类信息，放在同一个 JSON 数组里。\n"
    "① 稳定事实：用户自己明确说过的、以后聊天可能用到的长期信息"
    "（名字/喜好/所在地/工作学习/家人宠物/项目/约定/习惯等），"
    "每条写成 {{\"key\":\"主题\",\"value\":\"具体内容\"}}。\n"
    "② 近期处境：用户明确说出的、只持续一阵子的近况，包括客观事件"
    "（加班/备考/生病/搬家/出差/找工作）和用户自己说出口的情绪状态"
    "（好累/烦死了/压力大/崩溃），"
    "每条写成 {{\"kind\":\"situation\",\"situation\":\"类别\"}}，"
    "类别只能是：{kinds}。没有就别写这条。\n"
    "规则：\n"
    "1. 只写用户明说的，不写宠物的话、提问、玩笑、一次性查询；\n"
    "2. 情绪类只认用户第一人称明确说出的状态（如「我好累」「烦死了」），"
    "不推测、不脑补没说的（「他是不是压力大」不算）；\n"
    "3. 「我朋友加班」「你是不是很累」不是用户自己的处境，不要写；\n"
    "4. 没有值得写的就输出 []；事实最多 {limit} 条；\n"
    "5. 只输出严格的 JSON 数组，不要解释、不要 markdown 代码块。\n\n"
    "对话：\n{dialogue}"
)


def _parse_array(text: str) -> list[dict]:
    """宽容解析模型输出的 JSON 数组：去围栏、截取数组、坏输出返回空。"""
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1] if raw.count("```") >= 2 else raw.strip("`")
        if raw.lstrip().lower().startswith("json"):
            raw = raw.lstrip()[4:]
    start, end = raw.find("["), raw.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(raw[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return []
    return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []


def parse_facts(text: str, limit: int = MAX_FACTS_PER_CALL) -> list[tuple[str, str]]:
    """宽容解析模型输出里的稳定事实；处境条目（kind=situation）自动跳过，向后兼容。"""
    out: list[tuple[str, str]] = []
    for item in _parse_array(text)[:limit * 2]:
        if str(item.get("kind") or "") == "situation":
            continue
        key = str(item.get("key") or "").strip()[:24]
        value = str(item.get("value") or "").strip()[:120]
        if key and value:
            out.append((key, value))
            if len(out) >= limit:
                break
    return out


def parse_situations(text: str) -> list[str]:
    """从同一次输出里提取 AI 识别的近期处境类别（限白名单，防编造）。"""
    out: list[str] = []
    for item in _parse_array(text):
        if str(item.get("kind") or "") != "situation":
            continue
        kind = str(item.get("situation") or "").strip().lower()
        if kind in SITUATION_KINDS and kind not in out:
            out.append(kind)
    return out


class AutoFactExtractor:
    """观察对话流 → 异步抽取事实 → 写入 LongTermMemory。"""

    def __init__(self, memory, gateway, cfg_provider: Callable[[], object],
                 enabled_provider: Callable[[], bool],
                 buffer_limit: int = BUFFER_LIMIT,
                 situation_sink: Callable[[list[str]], None] | None = None):
        self._memory = memory
        self._gateway = gateway
        self._cfg = cfg_provider
        self._enabled = enabled_provider
        self._buffer_limit = max(2, int(buffer_limit))
        self._buffer: list[tuple[str, str]] = []
        self._lock = threading.Lock()
        self._inflight = False
        self._situation_sink = situation_sink   # 可选旁路：把 AI 识别的处境交给 SituationService

    def observe(self, role: str, content: str) -> None:
        """ChatService 每轮回调（worker 线程）：只动内存 + 触发后台线程。"""
        if not self._enabled():
            return
        text = str(content or "").strip()[:400]
        if not text:
            return
        with self._lock:
            self._buffer.append((str(role), text))
            enough = len(self._buffer) >= self._buffer_limit
        immediate = (role == "user" and len(text) <= 120
                     and any(c in text for c in _STATEMENT_CLUES)
                     and not any(q in text for q in _QUESTION_MARKS))
        if enough:
            self._take_and_extract()
        elif immediate and not self._inflight:
            # 只拿这一句问 AI，主缓冲继续攒给后续全量抽取；去重由 remember 保证
            self._extract_async([("user", text)])

    def flush(self, wait_s: float = 1.5) -> None:
        """退出前尽力把剩余对话抽一次（后台线程，最多等 wait_s 秒不拖慢退出）。"""
        if not self._enabled():
            return
        with self._lock:
            if not self._buffer:
                return
        thread = self._take_and_extract()
        if thread is not None:
            thread.join(timeout=max(0.0, wait_s))

    # ---------- 内部 ----------

    def _take_and_extract(self):
        with self._lock:
            if not self._buffer or self._inflight:
                return None
            pending = list(self._buffer)
            self._buffer.clear()
        return self._extract_async(pending)

    def _extract_async(self, pending: list[tuple[str, str]]):
        self._inflight = True

        def _work():
            try:
                self._extract(pending)
            except Exception:  # noqa: BLE001 - 记忆是增强，失败静默等下次
                logger.exception("auto fact extraction failed")
            finally:
                self._inflight = False

        thread = threading.Thread(target=_work, name="wisepet-facts", daemon=True)
        thread.start()
        return thread

    def _extract(self, pending: list[tuple[str, str]]) -> None:
        dialogue = "\n".join(f"{'用户' if r == 'user' else '宠物'}：{c}"
                             for r, c in pending)
        prompt = _PROMPT.format(limit=MAX_FACTS_PER_CALL, dialogue=dialogue,
                                kinds="/".join(sorted(SITUATION_KINDS)))
        raw = self._gateway.complete([{"role": "user", "content": prompt}],
                                     self._cfg())
        # 旁路：同一次调用里 AI 识别的近期处境交给 SituationService（有 Key 主路径）
        if self._situation_sink is not None:
            try:
                kinds = parse_situations(raw)
                if kinds:
                    self._situation_sink(kinds)
            except Exception:  # noqa: BLE001 - 处境是增强，失败不影响事实落盘
                logger.debug("situation sink failed", exc_info=True)
        facts = parse_facts(raw)
        if not facts:
            return
        known = {(str(e.get("key") or ""), str(e.get("value") or ""))
                 for e in self._memory.entries()}
        saved = 0
        for key, value in facts:
            if (key, value) in known:
                continue
            if self._memory.remember(key, value):
                known.add((key, value))
                saved += 1
        if saved:
            logger.info("auto facts learned: %d", saved)
