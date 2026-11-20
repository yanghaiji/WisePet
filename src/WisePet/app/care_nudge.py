"""指标驱动主动关怀：从「报表」变成「宠物行为」。

北极星护栏（有效互动天数）已经可测，本模块让数据会说话：当连续多天没有
有效互动（用户没聊/没确认提醒/没用工具），宠物主动问一句，而不是静静消失。
纯函数判断 + 话术，调度与去重交给组装根（每天最多一次、走 QuietBudget）。
"""
from __future__ import annotations

#: 连续多少天无有效互动才主动关怀（3 天：够明显又不烦）
NUDGE_AFTER_DAYS = 3


def should_nudge(days_since: int | None, *, first_seen: bool = True) -> bool:
    """该不该主动关怀：从未互动不打扰（新用户），沉默够久才问。"""
    if days_since is None:
        return False                    # 从未有效互动：可能是新用户，别一上来就追问
    return days_since >= NUDGE_AFTER_DAYS


def nudge_line(days_since: int, name: str = "小墨") -> str:
    """按沉默时长分级的话术（纯函数，宠物口吻，不卖惨不催促）。"""
    if days_since >= 7:
        return f"{name}有点想你啦，好几天没见你说话了。我一直在，忙完了来找我嘛～"
    return f"这两天好像没怎么聊呢，{name}在这儿等你哦。今天还好吗？"
