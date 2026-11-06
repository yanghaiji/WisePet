"""NewsService：定时拉 RSS 标题 →（可选）AI 摘要 → 气泡播报。

网络与 AI 调用在后台线程执行（httpx 阻塞不能碰主线程），结果经 EventBus →
QtEventBridge 转投回主线程，与聊天回复共用同一条投递路径。
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

from ..domain.events import SpeakRequestedEvent

logger = logging.getLogger(__name__)

_SECONDS_PER_MINUTE = 60.0
#: 气泡播报文本上限：太长会把 320px 宽的气泡顶成一屏
MAX_NEWS_CHARS = 600


class NewsService:
    def __init__(self, clock, bus, config_provider: Callable[[], object],
                 fetcher: Callable[[list[str]], list[str]],
                 summarizer: Callable[[list[str]], str | None] | None = None,
                 deferral_provider: Callable[[], float] | None = None):
        self._clock = clock
        self._bus = bus
        self._config = config_provider
        self._fetcher = fetcher
        self._summarizer = summarizer
        self._deferral = deferral_provider   # 勿扰时段：>0 表示顺延到时段结束
        self._next_at: float | None = None
        self._inflight = False

    # ---------- 命令 ----------

    def start(self) -> None:
        self._next_at = self._clock.now() + self._interval_s()

    def reschedule(self) -> None:
        """配置间隔变化后立即重排：不等旧周期走完（真机踩过：改 1 分钟没反应）。"""
        self._next_at = self._clock.now() + self._interval_s()

    def tick(self, now: float | None = None) -> None:
        """主循环每秒调用：到点且上一轮已完成时拉起新一轮（错峰由线程天然保证）。"""
        if self._next_at is None or self._inflight:
            return
        now = self._clock.now() if now is None else now
        if now < self._next_at:
            return
        if self._deferral is not None:
            defer_s = self._deferral()
            if defer_s > 0:
                self._next_at = now + max(30.0, defer_s)
                return
        self._next_at = now + self._interval_s()
        self._inflight = True
        threading.Thread(target=self._run_once, name="wisepet-news",
                         daemon=True).start()

    def refresh_now(self) -> None:
        """手动立刻拉一轮（右键菜单等入口预留）。"""
        if not self._inflight:
            self._inflight = True
            threading.Thread(target=self._run_once, name="wisepet-news",
                             daemon=True).start()

    # ---------- 内部 ----------

    def _interval_s(self) -> float:
        news = self._config().news
        return max(1.0, float(news.interval_min)) * _SECONDS_PER_MINUTE

    def _run_once(self) -> None:
        try:
            news = self._config().news
            if not news.enabled:
                return
            headlines = self._fetcher(list(news.sources))[:int(news.max_headlines)]
            if not headlines:
                logger.info("news refresh got no headlines（源可能超时/被拦）")
                return
            summary = None
            if self._summarizer is not None:
                summary = self._summarizer(headlines)
            text = summary or self._plain_text(headlines)
            self._bus.publish(SpeakRequestedEvent(text[:MAX_NEWS_CHARS],
                                                  source="news", title="新闻速览"))
            logger.info("news published headlines=%d ai_summary=%s",
                        len(headlines), summary is not None)
        except Exception:  # noqa: BLE001 - 后台线程兜底，绝不带崩主程序
            logger.exception("news refresh failed")
        finally:
            self._inflight = False

    @staticmethod
    def _plain_text(headlines: list[str]) -> str:
        lines = [f"{index}. {title}" for index, title in enumerate(headlines, 1)]
        return "新闻速览：\n" + "\n".join(lines)
