"""WeeklyReportService：每周回忆录——到设定的星期/时刻，基于本地聊天生成一条周报。

与 NewsService/CheerService 同款骨架：tick 判定到点 → 后台线程汇总 → EventBus 转投
主线程气泡；配了 AI 就把统计润色成宠物口吻，未配/失败则用本地模板，保证必有输出。
每个自然周只推一次（weekly.json 记录已推送的周标识），勿扰时段顺延。
"""
from __future__ import annotations

import datetime as _dt
import logging
import threading
import time
from collections import Counter
from typing import Callable

from ..domain.events import SpeakRequestedEvent
from .nlp import STOPWORDS, Tokenizer, bigram_split, has_cjk

logger = logging.getLogger(__name__)

#: 参与话题统计的最短词元长度（单字噪声大，过滤掉）
_MIN_TOPIC_LEN = 2


class WeeklyReportService:
    def __init__(self, clock, bus, config_provider: Callable[[], object],
                 records_provider: Callable[[], list], store,
                 summarizer: Callable[[dict], str | None] | None = None,
                 deferral_provider: Callable[[], float] | None = None,
                 tokenizer: Tokenizer | None = None):
        self._clock = clock
        self._bus = bus
        self._config = config_provider
        self._records = records_provider
        self._store = store
        self._summarizer = summarizer
        self._deferral = deferral_provider
        self._tokenizer = tokenizer or Tokenizer(bigram_split)
        self._marker = store.load()
        self._inflight = False

    # ---------- 命令 ----------

    def start(self) -> None:
        return None    # 无固定周期，靠 tick 判定「本周到点且未推送」

    def tick(self, now: float | None = None) -> None:
        if self._inflight:
            return
        weekly = getattr(self._config(), "weekly", None)
        if weekly is None or not weekly.enabled:
            return
        now = self._clock.now() if now is None else now
        local = time.localtime(now)
        if local.tm_wday != int(weekly.weekday) or local.tm_hour < int(weekly.hour):
            return
        week_id = _week_id(now)
        if self._marker.get("last_week") == week_id:
            return
        if self._deferral is not None and self._deferral() > 0:
            return     # 勿扰时段：本轮跳过，时段结束后下一个 tick 再补
        self._inflight = True
        threading.Thread(target=self._run_once, args=(week_id,),
                         name="wisepet-weekly", daemon=True).start()

    # ---------- 内部 ----------

    def _run_once(self, week_id: str) -> None:
        try:
            stats = summarize_week(self._records(), self._tokenizer)
            text = None
            if self._summarizer is not None:
                try:
                    text = self._summarizer(stats)
                except Exception:  # noqa: BLE001 - AI 失败走本地模板
                    logger.warning("weekly summarizer failed, use local template")
            text = (text or local_report(stats))[:600]
            self._bus.publish(SpeakRequestedEvent(text, source="system", title="本周回忆录"))
            self._mark(week_id)
            logger.info("weekly report published messages=%s", stats["messages"])
        except Exception:  # noqa: BLE001 - 后台线程兜底
            logger.exception("weekly report failed")
        finally:
            self._inflight = False

    def _mark(self, week_id: str) -> None:
        self._marker = {"last_week": week_id}
        self._store.save(self._marker)


def summarize_week(records: list, tokenizer: Tokenizer | None = None) -> dict:
    """纯统计：本周聊了多少句、活跃几天、问得最多的话题词。"""
    tokenizer = tokenizer or Tokenizer(bigram_split)
    messages = len(records)
    days = {r.session_id for r in records if getattr(r, "session_id", "")}
    if not days:
        days = {_day_of(r.ts) for r in records if getattr(r, "ts", 0)}
    counter: Counter[str] = Counter()
    for rec in records:
        if getattr(rec, "role", "") != "user":
            continue
        for tok in tokenizer.tokenize(getattr(rec, "content", "")):
            if len(tok) >= _MIN_TOPIC_LEN and tok not in STOPWORDS and has_cjk(tok):
                counter[tok] += 1
    top_topic, top_count = (counter.most_common(1)[0] if counter else ("", 0))
    return {"messages": messages, "active_days": len(days),
            "top_topic": top_topic if top_count >= 2 else "", "topics": counter}


def local_report(stats: dict) -> str:
    """未配 AI 时的本地周报文案。"""
    messages = stats.get("messages", 0)
    days = stats.get("active_days", 0)
    topic = stats.get("top_topic", "")
    if messages == 0:
        return "这一周我们安静地待在一起，你在的时候我就很开心～下周也要常来呀！"
    parts = [f"本周我们一共聊了 {messages} 句，有 {days} 天你在～"]
    if topic:
        parts.append(f"你问得最多的是「{topic}」。")
    parts.append("下周也继续一起加油吧！")
    return "".join(parts)


def _week_id(ts: float) -> str:
    d = _dt.date.fromtimestamp(ts)
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


def _day_of(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts or 0.0))
