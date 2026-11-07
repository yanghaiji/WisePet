"""本地产品指标：激活 / 留存 / 打扰护栏 / 功能渗透 / 崩溃，全部只落本机。

存在的理由：`docs/产品规划.md` 写了北极星「有效互动天数」，但项目此前一行埋点
都没有，指标根本算不出来，所有优先级只能靠拍脑袋。这里补的是**可测量性**，
不是监控：无网络调用、无内容采集、可一键清空、只保留近 30 天。
"""
from __future__ import annotations

import logging
import re
import sys
import time
import traceback
from collections import Counter
from datetime import date, timedelta
from typing import Callable

from ..domain.chat_record import day_key

logger = logging.getLogger(__name__)

RETENTION_DAYS = 30
_SK_TOKEN = re.compile(r"sk-[A-Za-z0-9_\-]{4,}")


def compute_metrics(data: dict, today: str) -> dict:
    """纯函数：从按天计数里算出北极星与护栏，便于不带 Qt/IO 单测。"""
    days = data.get("days") or {}
    firsts = data.get("firsts") or {}
    totals: Counter = Counter()
    for bucket in days.values():
        for name, count in (bucket or {}).items():
            totals[name] += int(count or 0)

    shown = totals.get("reminder_shown", 0)
    dismissed = totals.get("reminder_dismissed", 0)
    try:
        first_seen = date.fromisoformat(str(data.get("first_seen") or today))
        today_date = date.fromisoformat(today)
    except ValueError:
        first_seen = today_date = date.fromisoformat(today)
    d7_day = (first_seen + timedelta(days=7)).isoformat()
    d7_eligible = today_date >= first_seen + timedelta(days=7)

    feature = {name: count for name, count in sorted(totals.items())
               if name.startswith("cmd:") or name.startswith("feat:")}
    return {
        "ttfv_chat": "first_chat_done" in firsts,
        "ttfv_reminder": "first_reminder_done" in firsts,
        "active_days": len(days),
        "d7_eligible": d7_eligible,
        "d7_retained": d7_eligible and d7_day in days,
        "reminder_shown": shown,
        "reminder_dismissed": dismissed,
        "reminder_dismiss_rate": round(dismissed / shown, 3) if shown else 0.0,
        "crash_count": totals.get("app_crash", 0),
        "events_total": int(sum(totals.values())),
        "feature_reach": feature,
    }


class TelemetryService:
    def __init__(self, store, clock=None, now_provider: Callable[[], float] | None = None):
        self._store = store
        self._now = now_provider or (clock.now if clock is not None else time.time)
        self._day_key = lambda: day_key(self._now())
        self._data = self._store.load(self._now())
        self._prune()

    # ---------- 采集 ----------

    def track(self, name: str) -> None:
        """记一次事件；名字必须是枚举式常量，禁止把用户输入当事件名。"""
        name = str(name or "").strip()[:60]
        if not name:
            return
        day = self._day_key()
        bucket = self._data.setdefault("days", {}).setdefault(day, {})
        bucket[name] = int(bucket.get(name, 0)) + 1
        self._store.save(self._data)

    def once(self, name: str) -> bool:
        """首次事件（TTFV 用）；返回是否第一次发生。"""
        name = str(name or "").strip()[:60]
        if not name or name in self._data.setdefault("firsts", {}):
            return False
        self._data["firsts"][name] = self._day_key()
        self._store.save(self._data)
        self.track(name)
        return True

    def metrics(self) -> dict:
        return compute_metrics(self._data, self._day_key())

    def clear(self) -> None:
        self._data = self._store.reset(self._now())

    # ---------- 崩溃自愈 ----------

    def record_crash(self, exc_type, exc, tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        text = _SK_TOKEN.sub("***", text)          # 堆栈里也不留密钥明文
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self._now()))
        self._store.append_crash(f"=== {stamp} ===\n{text}")
        self.track("app_crash")
        logger.error("unhandled exception recorded locally:\n%s", text)

    def install_crash_hook(self) -> None:
        """包住既有 excepthook：先本地留痕，再交回原处理逻辑。"""
        previous = sys.excepthook

        def hook(exc_type, exc, tb):
            try:
                self.record_crash(exc_type, exc, tb)
            except Exception:  # noqa: BLE001 - 兜底不能再抛
                pass
            previous(exc_type, exc, tb)

        sys.excepthook = hook

    # ---------- 内部 ----------

    def _prune(self) -> None:
        """只保留近 30 天，避免常驻应用把埋点文件越攒越大。"""
        cutoff = (date.fromisoformat(self._day_key())
                  - timedelta(days=RETENTION_DAYS)).isoformat()
        days = self._data.get("days") or {}
        kept = {day: bucket for day, bucket in days.items() if day >= cutoff}
        if len(kept) != len(days):
            self._data["days"] = kept
            self._store.save(self._data)
