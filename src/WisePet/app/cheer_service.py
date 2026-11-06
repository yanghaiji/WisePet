"""CheerService：定时鼓励推送——积极的话 / 古诗名句 / 夸奖（配 AI 时生成）。

与 NewsService 同款骨架：到点后台线程干活，结果经 EventBus 转投主线程气泡。
未配 AI 或生成失败时回落本地语录，保证到点必有输出。
"""
from __future__ import annotations

import logging
import random
import threading
from typing import Callable

from ..domain.events import SpeakRequestedEvent

logger = logging.getLogger(__name__)

_SECONDS_PER_MINUTE = 60.0

#: 本地语录：古诗名句 + 夸奖（AI 未配置时的兜底，全部积极向上）
LOCAL_CHEER_LINES = (
    "长风破浪会有时，直挂云帆济沧海！",
    "会当凌绝顶，一览众山小～现在的你已经很棒啦！",
    "千里之行，始于足下。你今天又往前走了一步！",
    "莫愁前路无知己，天下谁人不识君！",
    "山重水复疑无路，柳暗花明又一村～再撑一下！",
    "今天的你也辛苦啦，摸摸头，给自己点个赞！",
    "你认真的样子真的很闪闪发光哦！",
    "休息是为了走更远的路，你已经做得很好了！",
    "天生我材必有用，自信一点，你可以的！",
    "不经一番寒彻骨，怎得梅花扑鼻香～继续加油！",
)


class CheerService:
    def __init__(self, clock, bus, config_provider: Callable[[], object],
                 generator: Callable[[], str | None] | None = None,
                 rng: random.Random | None = None,
                 deferral_provider: Callable[[], float] | None = None):
        self._clock = clock
        self._bus = bus
        self._config = config_provider
        self._generator = generator
        self._rng = rng or random.Random()
        self._deferral = deferral_provider   # 勿扰时段：>0 表示顺延到时段结束
        self._next_at: float | None = None
        self._inflight = False

    # ---------- 命令 ----------

    def start(self) -> None:
        self._next_at = self._clock.now() + self._interval_s()

    def reschedule(self) -> None:
        """配置间隔变化后立即重排，不等旧周期走完。"""
        self._next_at = self._clock.now() + self._interval_s()

    def tick(self, now: float | None = None) -> None:
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
        threading.Thread(target=self._run_once, name="wisepet-cheer",
                         daemon=True).start()

    # ---------- 内部 ----------

    def _interval_s(self) -> float:
        return max(1.0, float(self._config().cheer.interval_min)) * _SECONDS_PER_MINUTE

    def _run_once(self) -> None:
        try:
            cheer = self._config().cheer
            if not cheer.enabled:
                return
            text = None
            if self._generator is not None:
                try:
                    text = self._generator()
                except Exception:  # noqa: BLE001 - AI 挂了也走本地语录
                    logger.warning("cheer generator failed, use local lines")
            if not text:
                text = self._rng.choice(LOCAL_CHEER_LINES)
            self._bus.publish(SpeakRequestedEvent(str(text)[:200],
                                                  source="cheer", title="加油打气"))
            logger.info("cheer published ai_generated=%s", self._generator is not None)
        except Exception:  # noqa: BLE001 - 后台线程兜底
            logger.exception("cheer refresh failed")
        finally:
            self._inflight = False
