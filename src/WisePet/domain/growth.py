"""成长体系领域模型：互动积分 → 等级 → 形态（幼年/成长/成熟）。

纯函数、零三方依赖，只依赖累计积分这一事实；持久化在 infra 层。
设计取舍：等级按「逐级递增的积分门槛」线性累积，形态按等级分三段，
简单可预期（用户能理解「再喂几次就升级」），不引入外部数值曲线。
"""
from __future__ import annotations

from dataclasses import dataclass

#: 形态（stage）常量与分档等级
STAGE_BABY = "幼年期"
STAGE_TEEN = "成长期"
STAGE_ADULT = "成熟期"
TEEN_LEVEL = 4      # 达到该等级进入成长期
ADULT_LEVEL = 10    # 达到该等级进入成熟期

#: 每级所需积分随等级递增：升到 L+1 需要 BASE*L 积分
LEVEL_BASE_COST = 20

#: 各类互动的积分权重（喂饱/抚摸/聊天/每天陪伴满）
FEED_POINTS = 2
PET_POINTS = 1
CHAT_POINTS = 1
COMPANION_DAY_POINTS = 10


def stage_for_level(level: int) -> str:
    if level >= ADULT_LEVEL:
        return STAGE_ADULT
    if level >= TEEN_LEVEL:
        return STAGE_TEEN
    return STAGE_BABY


def level_from_points(points: int) -> int:
    """累计积分换等级：从 1 级起，逐级扣除递增门槛。非法/负值当 0 处理。"""
    remaining = max(0, int(points))
    level = 1
    while remaining >= LEVEL_BASE_COST * level:
        remaining -= LEVEL_BASE_COST * level
        level += 1
    return level


@dataclass(frozen=True)
class GrowthVO:
    """成长快照：等级、形态、当前级内进度（供统计面板只读展示）。"""
    points: int
    level: int
    stage: str
    level_points: int    # 本级已累计积分
    level_need: int      # 本级升满所需积分
    companion_days: int = 0

    @classmethod
    def from_points(cls, points: int, companion_days: int = 0) -> "GrowthVO":
        points = max(0, int(points))
        level = level_from_points(points)
        consumed = sum(LEVEL_BASE_COST * lv for lv in range(1, level))
        need = LEVEL_BASE_COST * level
        return cls(points=points, level=level, stage=stage_for_level(level),
                   level_points=points - consumed, level_need=need,
                   companion_days=max(0, int(companion_days)))

    @property
    def stage_progress(self) -> float:
        """本级完成度 0~1，面板可据此画进度。"""
        if self.level_need <= 0:
            return 1.0
        return max(0.0, min(1.0, self.level_points / self.level_need))
