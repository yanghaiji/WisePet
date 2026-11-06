"""状态值对象：饥饿/心情/精力三维数值与衰减规则（PRD M5 / 不变量 I4）。"""
from __future__ import annotations

from dataclasses import dataclass

_VALUE_MIN = 0.0
_VALUE_MAX = 100.0


def _clamp(value: float) -> float:
    return max(_VALUE_MIN, min(_VALUE_MAX, float(value)))


@dataclass(frozen=True)
class VitalsVO:
    """宠物状态值对象。不变量：所有分量钳制在 [0,100]。"""

    hunger: float = 80.0   # 饱食度，越高越饱
    mood: float = 80.0     # 心情
    energy: float = 80.0   # 精力

    def __post_init__(self):
        object.__setattr__(self, "hunger", _clamp(self.hunger))
        object.__setattr__(self, "mood", _clamp(self.mood))
        object.__setattr__(self, "energy", _clamp(self.energy))

    def decayed(self, dt_hours: float, rules: "DecayRules", is_night: bool,
                is_sleeping: bool = False) -> "VitalsVO":
        """按经过的小时数线性衰减；不低于 floor（当前值低于 floor 时不再拉低）。

        睡着时精力不降反升（走 `rested`，PRD v1.7 C9）：饱食与心情照旧掉（睡觉不吃饭），
        但「夜间且精力低 → 睡眠」得是个自我修复闭环，否则越睡越困、天亮接着求助。
        """
        if dt_hours <= 0:
            return self
        if is_sleeping:
            energy = self.rested(rules.energy_recover_per_hour * dt_hours).energy
        else:
            night_factor = rules.energy_night_factor if is_night else 1.0
            energy = self._decay_one(self.energy,
                                     rules.energy_per_hour * night_factor * dt_hours,
                                     rules.floor)
        return VitalsVO(
            hunger=self._decay_one(self.hunger, rules.hunger_per_hour * dt_hours, rules.floor),
            mood=self._decay_one(self.mood, rules.mood_per_hour * dt_hours, rules.floor),
            energy=energy,
        )

    @staticmethod
    def _decay_one(value: float, amount: float, floor: float) -> float:
        lower = min(value, floor)  # 数值已低于下限时保持原值（只降不升）
        return max(value - amount, lower)

    def fed(self, amount: float = 20.0) -> "VitalsVO":
        return VitalsVO(self.hunger + amount, self.mood, self.energy)

    def petted(self, amount: float = 10.0) -> "VitalsVO":
        return VitalsVO(self.hunger, self.mood + amount, self.energy)

    def hurt_by(self, amount: float = 5.0) -> "VitalsVO":
        return VitalsVO(self.hunger, self.mood - amount, self.energy)

    def rested(self, amount: float = 30.0) -> "VitalsVO":
        """睡够了：只回精力，上限 100（钳位在 `__post_init__`，不在此手算）。"""
        return VitalsVO(self.hunger, self.mood, self.energy + amount)


@dataclass(frozen=True)
class DecayRules:
    """衰减参数（可被用户配置覆盖，默认值来自 PRD）。"""

    hunger_per_hour: float = 4.2      # 约 24h 从 100 衰减到接近 0
    mood_per_hour: float = 2.0
    energy_per_hour: float = 3.0
    energy_night_factor: float = 2.0  # 夜间精力衰减加倍（只适用于熬夜醒着时，睡着走回血）
    energy_recover_per_hour: float = 8.0   # 睡着时每小时回的精力：睡 8h 约 +64，从见底回到接近满
    floor: float = 10.0               # 衰减下限：不会"饿死"
