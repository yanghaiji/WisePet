"""有效互动口径与护栏阈值：把「北极星」从拍脑袋变成可复算的纯逻辑。

北极星 = 有效互动天数（发过消息 / 用过工具 / 确认过提醒），
与「活跃天数」（任何埋点都算）刻意区分：后者会把一次崩溃也算成活跃，
把北极星算虚高。护栏是「不能为了北极星牺牲的东西」，供看板红黄绿灯。
"""
from __future__ import annotations

#: 计入「有效互动」的事件；只有这些事件出现的日子才算真正互动过
ENGAGEMENT_EVENTS: frozenset[str] = frozenset({
    "msg:sent", "tool:used", "reminder:confirmed",
})

#: 护栏阈值（违反即 warn）
DISMISS_RATE_LIMIT = 0.5      # 提醒关闭率 > 50% = 打扰失衡
FALLBACK_RATE_LIMIT = 0.5     # 模型降级率 > 50% = 大脑配置基本不可用
CRASH_LIMIT = 3               # 崩溃次数


def engaged_days(days: dict[str, dict[str, int]]) -> int:
    """统计出现过至少一次有效互动事件的天数。"""
    return sum(1 for bucket in days.values()
               if bucket and any(name in ENGAGEMENT_EVENTS for name in bucket))


def _rate_warn(value: float | None, limit: float) -> str:
    if value is None:
        return "n/a"
    return "warn" if float(value) > limit else "ok"


def guardrail_status(metrics: dict) -> dict[str, str]:
    """返回每个护栏状态：ok / warn / n/a。纯函数，供看板红黄绿灯。"""
    status: dict[str, str] = {}
    status["disturbance"] = _rate_warn(
        metrics.get("reminder_dismiss_rate"), DISMISS_RATE_LIMIT)
    status["brain"] = _rate_warn(
        metrics.get("ai_fallback_rate"), FALLBACK_RATE_LIMIT)
    crash = metrics.get("crash_count")
    status["stability"] = ("warn" if crash and int(crash) >= CRASH_LIMIT
                           else "ok")
    if not metrics.get("d7_eligible"):
        status["retention"] = "n/a"
    else:
        status["retention"] = "ok" if metrics.get("d7_retained") else "warn"
    return status


GUARDRAIL_LABELS: tuple[tuple[str, str], ...] = (
    ("disturbance", "打扰平衡"),
    ("brain", "大脑可用"),
    ("stability", "运行稳定"),
    ("retention", "七日留存"),
)
