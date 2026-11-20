"""WeatherService：后台抓天气 + 缓存 + 判断该不该报（不阻塞主 tick）。

主循环只调 `tick(now)`（纯读缓存、零网络）；真正的 HTTP 抓取丢后台线程，
失败静默、下次再试。抓取间隔与每日播报都按自然日去重，坏天气可在播报点之外
额外提醒一次（仍由上层 EventTrigger 的情境分与 QuietBudget 把关）。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from ..domain.weather import care_line, is_bad_weather
from ..infra.weather_api import WeatherGateway, WeatherSnapshot

logger = logging.getLogger(__name__)

#: 两次抓取的最小间隔（秒）：天气变化慢，别频繁打公共 API
FETCH_INTERVAL_S = 30 * 60


class WeatherService:
    def __init__(self, gateway: WeatherGateway, config_provider,
                 enabled_provider: Callable[[], bool] | None = None,
                 now_provider: Callable[[], float] | None = None,
                 fetcher=None):
        self._gateway = gateway
        self._config = config_provider
        self._enabled = enabled_provider
        self._now = now_provider or time.time
        self._fetcher = fetcher or self._fetch_in_thread
        self._snapshot: WeatherSnapshot | None = None
        self._last_fetch: float = 0.0
        self._last_report_ts: float = 0.0  # 上次播报时刻（间隔模式去重）
        self._bad_alerted_day: str = ""    # 坏天气额外提醒当日已发
        self._last_report_day: str = ""    # 跨日重置间隔计时用
        self._lock = threading.Lock()

    def is_enabled(self) -> bool:
        if self._enabled is not None:
            return bool(self._enabled())
        try:
            return bool(getattr(self._config(), "weather").enabled)
        except Exception:  # noqa: BLE001
            return False

    def request_fetch(self, force: bool = False) -> None:
        """请求一次后台抓取；距离上次太近则跳过（除非 force）。"""
        if not self.is_enabled():
            return
        now = self._now()
        with self._lock:
            if not force and now - self._last_fetch < FETCH_INTERVAL_S:
                return
            self._last_fetch = now
        self._fetcher(self._do_fetch)

    def _fetch_in_thread(self, fn) -> None:
        threading.Thread(target=fn, daemon=True, name="wisePet-weather").start()

    def _do_fetch(self) -> None:
        snapshot = self._gateway.fetch()
        if snapshot is not None:
            with self._lock:
                self._snapshot = snapshot

    def snapshot(self) -> WeatherSnapshot | None:
        with self._lock:
            return self._snapshot

    def due_line(self, now: float | None = None, *,
                 day_key_fn: Callable[[float], str]) -> str:
        """若此刻该播报，返回一句关怀话术；否则返回空串（纯读缓存）。

        规则：每天播报点后播一次常规天气；坏天气当日可额外再提醒一次。
        """
        if not self.is_enabled():
            return ""
        snap = self.snapshot()
        if snap is None:
            return ""
        now = self._now() if now is None else now
        day = day_key_fn(now)
        try:
            wcfg = getattr(self._config(), "weather")
            interval_s = max(30.0, float(wcfg.interval_min)) * 60.0
            bad_alert = bool(wcfg.bad_weather_alert)
        except Exception:  # noqa: BLE001
            interval_s, bad_alert = 180.0 * 60.0, True
        # 跨日重置：新的一天允许立即报一次（像「早上第一句天气」）
        if day != self._last_report_day:
            self._last_report_ts = 0.0
            self._last_report_day = day
        due = (self._last_report_ts <= 0.0
               or now - self._last_report_ts >= interval_s)
        def broadcast() -> str:
            return care_line(
                code=snap.code, temp=snap.temp, city=snap.city,
                humidity=getattr(snap, "humidity", ""),
                low=getattr(snap, "low", ""), high=getattr(snap, "high", ""))

        line = ""
        if due:
            line = broadcast()
            self._last_report_ts = now
        elif (bad_alert and is_bad_weather(snap.code)
              and day != self._bad_alerted_day):
            # 坏天气在两次常规播报之间额外提醒一次（仍按自然日去重）
            line = broadcast()
            self._bad_alerted_day = day
        return line
