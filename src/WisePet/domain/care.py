"""主动关怀的本地纯逻辑：节气识别 / 情境评分 / 打扰反馈降频。

刻意不接天气/日历/联网 API（与零依赖、断网可用底线一致）：节气用公历近似
日期表，只做「到点说句话」，不声称精确天文时刻。所有规则都只是「候选」，
真正发不发仍由组装根统一过 QuietBudget —— 这里不开打扰后门。
"""
from __future__ import annotations

import time

#: 二十四节气的公历近似日（月, 日）→ 名称，误差约 ±1 天，够桌面关怀用
_SOLAR_TERMS: tuple[tuple[int, int, str], ...] = (
    (1, 6, "小寒"), (1, 20, "大寒"), (2, 4, "立春"), (2, 19, "雨水"),
    (3, 6, "惊蛰"), (3, 21, "春分"), (4, 5, "清明"), (4, 20, "谷雨"),
    (5, 6, "立夏"), (5, 21, "小满"), (6, 6, "芒种"), (6, 21, "夏至"),
    (7, 7, "小暑"), (7, 23, "大暑"), (8, 8, "立秋"), (8, 23, "处暑"),
    (9, 8, "白露"), (9, 23, "秋分"), (10, 8, "寒露"), (10, 23, "霜降"),
    (11, 7, "立冬"), (11, 22, "小雪"), (12, 7, "大雪"), (12, 22, "冬至"),
)

#: 适合主动说点什么的节气（挑有情绪价值/生活提示的，而非全部 24 个）
CARE_TERMS: frozenset[str] = frozenset({
    "立春", "春分", "立夏", "夏至", "立秋", "秋分", "立冬", "冬至", "大寒", "大暑",
})


def solar_term(ts: float | None = None) -> str | None:
    """返回当天的节气名（近似）；不在表上返回 None。"""
    local = time.localtime(ts)
    for month, day, name in _SOLAR_TERMS:
        if local.tm_mon == month and local.tm_mday == day:
            return name
    return None


def care_term(ts: float | None = None) -> str | None:
    """仅返回值得关怀的节气名，其余日子返回 None。"""
    term = solar_term(ts)
    return term if term in CARE_TERMS else None


def context_score(*, hour: int, focusing: bool = False, quiet: bool = False,
                  dismiss_rate: float | None = None) -> float:
    """此刻主动关怀的合适度 0.0~1.0：专注/勿扰直接 0，深夜与高忽略率降权。

    纯函数，供事件触发在发话前打一道分；分数低于阈值就不打扰。
    """
    if focusing or quiet:
        return 0.0
    score = 1.0
    if hour >= 23 or hour < 7:
        score -= 0.6
    if dismiss_rate is not None and float(dismiss_rate) > 0.5:
        score -= 0.4
    return max(0.0, min(1.0, score))


def suggested_interval(base_min: float, dismiss_rate: float | None) -> float:
    """打扰反馈闭环：忽略率过半就把周期提醒间隔拉长一倍，否则原样。

    「用户老关掉提醒」是打扰失衡的强信号（护栏），降频比反复弹更体贴。
    """
    base = max(1.0, float(base_min))
    if dismiss_rate is not None and float(dismiss_rate) > 0.5:
        return round(base * 2.0, 3)
    return base
