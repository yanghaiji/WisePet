"""行为状态机：迁移白名单 + 锁定态（不变量 I1/I2）。

状态机只做合法性校验与迁移记录；计时与触发时机由 app 层驱动，
domain 不持时钟、不持线程。
"""
from __future__ import annotations

from enum import Enum, auto

from .errors import IllegalTransitionError


class BehaviorState(Enum):
    IDLE = auto()      # 待机
    WALKING = auto()   # 自主走动
    RUNNING = auto()   # 小跑（溜达途中偶发提速，PRD v2.0 C11）
    STRETCHING = auto()  # 伸懒腰（睡醒与待机偶发，PRD v2.0 C11）
    DRAGGING = auto()  # 被用户拖拽
    HURT = auto()      # 被戳反应
    EATING = auto()    # 进食
    THINKING = auto()  # 等待 AI 应答
    SPEAKING = auto()  # 气泡说话
    SLEEPING = auto()  # 睡眠
    SAD = auto()       # 低落（心情低于阈值）


#: 需要用户/驱动方之外的"自主"发起的行为目标，锁定态下禁止
AUTONOMOUS_TARGETS = frozenset({
    BehaviorState.WALKING,
    BehaviorState.RUNNING,
    BehaviorState.STRETCHING,
    BehaviorState.SLEEPING,
    BehaviorState.SAD,
})

#: 锁定态：进行期间禁止自主行为切换（不变量 I2）
LOCKED_STATES = frozenset({
    BehaviorState.DRAGGING,
    BehaviorState.THINKING,
    BehaviorState.SPEAKING,
})

#: 迁移白名单（不变量 I1）
ALLOWED_TRANSITIONS: dict[BehaviorState, frozenset] = {
    BehaviorState.IDLE: frozenset({BehaviorState.WALKING, BehaviorState.RUNNING,
                                   BehaviorState.STRETCHING, BehaviorState.SLEEPING,
                                   BehaviorState.HURT, BehaviorState.DRAGGING,
                                   BehaviorState.EATING, BehaviorState.THINKING,
                                   BehaviorState.SPEAKING, BehaviorState.SAD}),
    BehaviorState.WALKING: frozenset({BehaviorState.IDLE, BehaviorState.RUNNING,
                                      BehaviorState.SAD}),
    # 跑累了允许降回走动而不是急停（拖拽不在这张表里，见 can_transition）
    BehaviorState.RUNNING: frozenset({BehaviorState.IDLE, BehaviorState.WALKING,
                                      BehaviorState.SAD}),
    BehaviorState.STRETCHING: frozenset({BehaviorState.IDLE, BehaviorState.SAD}),
    BehaviorState.DRAGGING: frozenset({BehaviorState.IDLE}),
    BehaviorState.HURT: frozenset({BehaviorState.IDLE, BehaviorState.SAD}),
    BehaviorState.EATING: frozenset({BehaviorState.IDLE, BehaviorState.SAD}),
    BehaviorState.THINKING: frozenset({BehaviorState.SPEAKING, BehaviorState.IDLE}),
    BehaviorState.SPEAKING: frozenset({BehaviorState.IDLE}),
    # 睡醒先伸个懒腰再回待机（PRD v2.0 C11）
    BehaviorState.SLEEPING: frozenset({BehaviorState.IDLE, BehaviorState.STRETCHING,
                                       BehaviorState.SAD}),
    BehaviorState.SAD: frozenset({BehaviorState.IDLE, BehaviorState.WALKING}),
    # 难过只允许慢慢走（WALKING），不允许小跑：低心情还一溜小跑不像话；
    # 但必须能走：否则没人投喂时它会永远卡在 SAD 定在原地（实测踩过）。
}


class BehaviorStateMachine:
    def __init__(self, initial: BehaviorState = BehaviorState.IDLE):
        self._state = initial
        self.history: list[BehaviorState] = [initial]

    @property
    def state(self) -> BehaviorState:
        return self._state

    def can_transition(self, target: BehaviorState, autonomous: bool = False) -> bool:
        if target is self._state:
            return False
        # 拖拽是直接操纵，不受“锁定态禁止自主切换”（不变量 I2）限制：那条管的是
        # 宠物自己能不能改行为，管不着用户的手。实测心情 25.7 / 阈值 25.0 时它长期
        # 卡在 SAD，而白名单里 SAD 只能去 IDLE，于是桌面宠物整个拖不动。
        # THINKING / SPEAKING 也一并放行：那期间它只是在等回复，不拦着手。不写进
        # ALLOWED_TRANSITIONS 是因为那张表还要用来表达“自主可达”，拖拽不属于它。
        if target is BehaviorState.DRAGGING:
            return True
        if autonomous and self._state in LOCKED_STATES:
            return False
        return target in ALLOWED_TRANSITIONS[self._state]

    def transition(self, target: BehaviorState, autonomous: bool = False) -> BehaviorState:
        if not self.can_transition(target, autonomous):
            raise IllegalTransitionError(self._state, target)
        self._state = target
        self.history.append(target)
        return target
