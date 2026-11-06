"""位置值对象与朝向（PRD M3 边界钳制）。"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

Bounds = tuple  # (min_x, min_y, max_x, max_y)


class Facing(Enum):
    LEFT = "left"
    RIGHT = "right"

    def flipped(self) -> "Facing":
        return Facing.LEFT if self is Facing.RIGHT else Facing.RIGHT


@dataclass(frozen=True)
class PositionVO:
    """宠物在虚拟桌面坐标系中的位置。不变量：由 bounds 钳制在合法区域。"""

    x: int = 0
    y: int = 0

    def clamped_to(self, bounds: Bounds) -> "PositionVO":
        min_x, min_y, max_x, max_y = bounds
        return PositionVO(
            x=max(min_x, min(max_x, self.x)),
            y=max(min_y, min(max_y, self.y)),
        )

    def shifted(self, dx: int, dy: int) -> "PositionVO":
        return PositionVO(self.x + dx, self.y + dy)


def gravity_step(pos_y: int, floor_y: int, dt: float, fall_speed_px_per_s: float) -> tuple[int, bool]:
    """贴地重力单步推进：返回 ``(新的 y, 本次是否刚好落地)``（PRD v2.0 C10）。

    屏幕坐标 y 轴向下为正，``floor_y`` 是工作区下沿换算出的合法最大 y（即贴地线）。
    纯函数：不持时钟，``dt`` 由 app 层传入，domain 只算这一步该落到哪。

    - 已在地上（``pos_y >= floor_y``）：原地不动且 ``landed=False``，避免每 tick 都报一次落地
    - 过冲：直接收敛到 ``floor_y``，不做反弹物理——持续抖动比“掉不下来”更难看
    """
    if pos_y >= floor_y:
        return pos_y, False
    new_y = pos_y + int(round(max(0.0, dt) * fall_speed_px_per_s))
    if new_y >= floor_y:
        return floor_y, True
    return new_y, False
