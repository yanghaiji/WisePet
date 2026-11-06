"""TtsAnnouncer：把「该不该读、用什么音量语速读」的决定收在一处。

组装根只调用 `announce(text)`；本类读配置的 tts 段决定是否启用，并把朗读丢到
后台线程（synthesizer 可能阻塞数秒，绝不能卡住 UI 线程）。synthesizer 本身
由 infra.create_synthesizer 按平台装配，跨平台差异不在这里。
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

from ..infra.tts import strip_emoji, truncate

logger = logging.getLogger(__name__)


class TtsAnnouncer:
    def __init__(self, synthesizer, config_provider: Callable[[], object]):
        self._synth = synthesizer
        self._config = config_provider

    def announce(self, text: str) -> None:
        tts = getattr(self._config(), "tts", None)
        if tts is None or not tts.enabled:
            return
        text = truncate(strip_emoji(str(text or ""))).strip()
        if not text:
            return
        volume, rate = int(tts.volume), int(tts.rate)
        threading.Thread(target=self._speak, args=(text, volume, rate),
                         name="wisepet-tts", daemon=True).start()

    def _speak(self, text: str, volume: int, rate: int) -> None:
        try:
            self._synth.speak(text, volume=volume, rate=rate)
        except Exception:  # noqa: BLE001 - 朗读失败不打扰主流程
            logger.debug("tts announce failed", exc_info=True)
