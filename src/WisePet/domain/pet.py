"""Pet 聚合根：状态值对象 + 位置 + 行为状态机 + 衰减结算（PRD M2/M5）。"""
from __future__ import annotations

from .position import Bounds, Facing, PositionVO
from .state_machine import BehaviorState, BehaviorStateMachine
from .vitals import DecayRules, VitalsVO

#: 行为对数值的影响量（魔法数字集中定义）
FEED_HUNGER_GAIN = 20.0
PET_HEAD_MOOD_GAIN = 10.0
HURT_MOOD_LOSS = 5.0


class Pet:
    """聚合根。不变量 I3：vitals 只能通过本类的行为方法更新。"""

    def __init__(self, pet_id: str, name: str,
                 vitals: VitalsVO | None = None,
                 position: PositionVO | None = None,
                 state_machine: BehaviorStateMachine | None = None):
        self.pet_id = pet_id
        self.name = name
        self._vitals = vitals or VitalsVO()
        self.position = position or PositionVO()
        self.facing = Facing.RIGHT
        self._state_machine = state_machine or BehaviorStateMachine()
        self.last_updated: float | None = None

    # ---------- 只读视图 ----------

    @property
    def vitals(self) -> VitalsVO:
        return self._vitals

    @property
    def state(self) -> BehaviorState:
        return self._state_machine.state

    @property
    def autonomy_locked(self) -> bool:
        """拖拽/思考/说话期间，自主行为被冻结（不变量 I2）。"""
        return self._state_machine.state in {
            BehaviorState.DRAGGING, BehaviorState.THINKING, BehaviorState.SPEAKING}

    def can_autonomous(self) -> bool:
        return not self.autonomy_locked

    def is_feeling_sad(self, sad_threshold: float) -> bool:
        return self._vitals.mood < sad_threshold

    # ---------- 行为命令 ----------

    def _go(self, target: BehaviorState, autonomous: bool = False) -> BehaviorState:
        return self._state_machine.transition(target, autonomous)

    def start_walk(self) -> None:
        self._go(BehaviorState.WALKING, autonomous=True)

    def stop_walk(self) -> None:
        """位移行为被打断：先回待机再接受其它动作（小跑与走动同待遇）。"""
        if self.state in {BehaviorState.WALKING, BehaviorState.RUNNING}:
            self._go(BehaviorState.IDLE)

    def transition(self, target: BehaviorState, autonomous: bool = False) -> None:
        """对外暴露的状态迁移入口（供 app 层编排）。"""
        self._go(target, autonomous)

    def hurt(self) -> None:
        self._go(BehaviorState.HURT)
        self._vitals = self._vitals.hurt_by(HURT_MOOD_LOSS)

    def feed(self) -> None:
        self._go(BehaviorState.EATING)
        self._vitals = self._vitals.fed(FEED_HUNGER_GAIN)

    def lift_mood_to(self, floor_value: float) -> bool:
        """启动托底：快照心情低于 floor 时拉回（回来看到的是积极的宠物）。

        返回是否发生变化；其它维度不动。
        """
        if self._vitals.mood >= floor_value:
            return False
        self._vitals = VitalsVO(self._vitals.hunger, floor_value, self._vitals.energy)
        return True

    def pet_head(self) -> None:
        """抚摸：只提升心情，不打断当前行为。"""
        self._vitals = self._vitals.petted(PET_HEAD_MOOD_GAIN)

    def finish_action(self) -> None:
        """驱动方显式结束一次性动作（动画播完/应答到达）。"""
        if self.state in {BehaviorState.HURT, BehaviorState.EATING,
                          BehaviorState.SPEAKING, BehaviorState.SLEEPING,
                          BehaviorState.STRETCHING, BehaviorState.SAD}:
            self._go(BehaviorState.IDLE)

    # ---------- 拖拽（PRD M3） ----------

    def start_drag(self) -> None:
        self._go(BehaviorState.DRAGGING)

    def drag_by(self, dx: int, dy: int) -> None:
        self.position = self.position.shifted(dx, dy)

    def release_drag(self, bounds: Bounds) -> PositionVO:
        """松手落位：钳制到合法区域并回待机。"""
        self.position = self.position.clamped_to(bounds)
        if self.state is BehaviorState.DRAGGING:
            self._go(BehaviorState.IDLE)
        return self.position

    # ---------- 聊天联动（PRD M4） ----------

    def start_thinking(self) -> None:
        self._go(BehaviorState.THINKING)

    def start_speaking(self) -> None:
        self._go(BehaviorState.SPEAKING)

    # ---------- 时间结算（不变量 I4） ----------

    def settle_decay(self, now: float, rules: DecayRules, is_night: bool) -> VitalsVO:
        """按持久化时间戳补算衰减。返回结算后的 vitals；时间回拨不衰减也不恢复。

        睡着的那段时间精力走回血而不是衰减（PRD v1.7 C9）：本方法只看当前状态，
        不引入新时间轴——快照里不存行为状态，开机恢复后仍是待机，
        所以关机期间那段夜间时间按普通夜间衰减算。
        """
        if self.last_updated is None:
            self.last_updated = now
            return self._vitals
        if now < self.last_updated:  # 时钟回拨：保留较新时间戳，跳过本次
            return self._vitals
        dt_hours = (now - self.last_updated) / 3600.0
        if dt_hours > 0:
            self._vitals = self._vitals.decayed(
                dt_hours, rules, is_night,
                is_sleeping=self.state is BehaviorState.SLEEPING)
        self.last_updated = now
        return self._vitals

    # ---------- 供 AI 人设组装（PRD C3） ----------

    def vitals_summary(self) -> str:
        return (f"{self.name}：饱食(hunger)={self._vitals.hunger:.0f} "
                f"心情(mood)={self._vitals.mood:.0f} "
                f"精力(energy)={self._vitals.energy:.0f}")

    def snapshot(self) -> dict:
        """状态快照，供 PetStateRepository 持久化。"""
        return {
            "pet_id": self.pet_id,
            "name": self.name,
            "vitals": {"hunger": self._vitals.hunger,
                       "mood": self._vitals.mood,
                       "energy": self._vitals.energy},
            "position": {"x": self.position.x, "y": self.position.y},
            "facing": self.facing.value,
            "last_updated": self.last_updated,
        }

    @classmethod
    def from_snapshot(cls, data: dict) -> "Pet":
        vitals = data.get("vitals") or {}
        position = data.get("position") or {}
        pet = cls(
            pet_id=data.get("pet_id", "mochi"),
            name=data.get("name", "小墨"),
            vitals=VitalsVO(vitals.get("hunger", 80.0),
                            vitals.get("mood", 80.0),
                            vitals.get("energy", 80.0)),
            position=PositionVO(int(position.get("x", 0)), int(position.get("y", 0))),
        )
        pet.last_updated = data.get("last_updated")
        facing_value = data.get("facing")
        if facing_value in (Facing.LEFT.value, Facing.RIGHT.value):
            pet.facing = Facing(facing_value)
        return pet
