"""行为状态机测试：迁移白名单、锁定态、自主行为限制（不变量 I1/I2）。"""
import pytest

from WisePet.domain.errors import IllegalTransitionError
from WisePet.domain.state_machine import (
    ALLOWED_TRANSITIONS,
    AUTONOMOUS_TARGETS,
    BehaviorState,
    BehaviorStateMachine,
)

ALL_STATES = list(BehaviorState)


class TestBehaviorStateMachine:
    def should_start_in_idle_when_created(self):
        assert BehaviorStateMachine().state is BehaviorState.IDLE

    def should_transition_idle_to_walking_when_walk_requested(self):
        sm = BehaviorStateMachine()
        sm.transition(BehaviorState.WALKING, autonomous=True)
        assert sm.state is BehaviorState.WALKING

    def should_reject_walking_to_eating_when_not_allowed(self):
        sm = BehaviorStateMachine(initial=BehaviorState.WALKING)
        with pytest.raises(IllegalTransitionError):
            sm.transition(BehaviorState.EATING)

    def should_allow_dragging_from_every_state_but_dragging(self):
        """拖拽是直接操纵：除了已经在手里，任何状态都得能抓起来。

        曾经白名单里 SAD/HURT/EATING/STRETCHING/SLEEPING 都不指向 DRAGGING，
        心情长期低于阈值时宠物卡在 SAD，在桌面上整个拖不动。
        """
        for src in ALL_STATES:
            sm = BehaviorStateMachine(initial=src)
            if src is BehaviorState.DRAGGING:
                with pytest.raises(IllegalTransitionError):
                    sm.transition(BehaviorState.DRAGGING)
                continue
            sm.transition(BehaviorState.DRAGGING, autonomous=True)
            assert sm.state is BehaviorState.DRAGGING, src

    def should_still_block_autonomous_dragging_from_locked_state(self):
        """拖拽只对“用户发起”放行：宠物自己不能把自己变成被拖着（不变量 I2）。"""
        sm = BehaviorStateMachine(initial=BehaviorState.THINKING)
        assert not sm.can_transition(BehaviorState.SLEEPING, autonomous=True)

    def should_allow_sad_from_any_uncovered_state_when_mood_low(self):
        """SAD 可从任意非锁定态进入；锁定态（I2）不允许被打断。"""
        locked = {BehaviorState.DRAGGING, BehaviorState.THINKING, BehaviorState.SPEAKING}
        for src in ALL_STATES:
            if src is BehaviorState.SAD or src in locked:
                continue
            sm = BehaviorStateMachine(initial=src)
            sm.transition(BehaviorState.SAD, autonomous=True)
            assert sm.state is BehaviorState.SAD

    def should_reject_sad_when_in_locked_state(self):
        sm = BehaviorStateMachine(initial=BehaviorState.DRAGGING)
        with pytest.raises(IllegalTransitionError):
            sm.transition(BehaviorState.SAD, autonomous=True)

    def should_block_autonomous_transition_when_locked_state(self):
        """不变量 I2：DRAGGING/THINKING/SPEAKING 期间禁止自主行为切换。"""
        for locked in [BehaviorState.DRAGGING, BehaviorState.THINKING, BehaviorState.SPEAKING]:
            sm = BehaviorStateMachine(initial=locked)
            for target in AUTONOMOUS_TARGETS:
                with pytest.raises(IllegalTransitionError):
                    sm.transition(target, autonomous=True)

    def should_allow_explicit_completion_from_locked_state(self):
        """锁定态可由驱动方显式结束回 IDLE（非自主迁移不受 I2 限制）。"""
        sm = BehaviorStateMachine(initial=BehaviorState.THINKING)
        sm.transition(BehaviorState.SPEAKING)
        sm.transition(BehaviorState.IDLE)
        assert sm.state is BehaviorState.IDLE

    def should_record_transition_history_when_changed(self):
        sm = BehaviorStateMachine()
        sm.transition(BehaviorState.HURT)
        sm.transition(BehaviorState.IDLE)
        assert sm.history == [BehaviorState.IDLE, BehaviorState.HURT, BehaviorState.IDLE]


class TestV2LocomotionStates:
    """v2.0 新增 RUNNING / STRETCHING（PRD C11）：白名单逐条定死，允许与拒绝都要测。"""

    @pytest.mark.parametrize("src,dst", [
        (BehaviorState.IDLE, BehaviorState.RUNNING),
        (BehaviorState.IDLE, BehaviorState.STRETCHING),
        (BehaviorState.WALKING, BehaviorState.RUNNING),
        (BehaviorState.RUNNING, BehaviorState.IDLE),
        (BehaviorState.RUNNING, BehaviorState.WALKING),      # 跑累了降回走，不急停
        (BehaviorState.STRETCHING, BehaviorState.IDLE),
        (BehaviorState.SLEEPING, BehaviorState.STRETCHING),  # 睡醒伸懒腰
        (BehaviorState.SAD, BehaviorState.WALKING),          # 难过也能慢慢走两步
    ])
    def should_allow_declared_v2_transitions(self, src, dst):
        sm = BehaviorStateMachine(initial=src)
        sm.transition(dst, autonomous=True)
        assert sm.state is dst

    @pytest.mark.parametrize("src,dst", [
        (BehaviorState.STRETCHING, BehaviorState.RUNNING),
        (BehaviorState.RUNNING, BehaviorState.STRETCHING),
        (BehaviorState.RUNNING, BehaviorState.THINKING),     # 聊天只能从待机进
        (BehaviorState.WALKING, BehaviorState.STRETCHING),   # 走途中不插播懒腰
        (BehaviorState.EATING, BehaviorState.RUNNING),
        (BehaviorState.SAD, BehaviorState.RUNNING),          # 低落只能先回待机
    ])
    def should_reject_undeclared_v2_transitions(self, src, dst):
        sm = BehaviorStateMachine(initial=src)
        with pytest.raises(IllegalTransitionError):
            sm.transition(dst, autonomous=True)

    def should_treat_new_states_as_autonomous_targets(self):
        """新状态归入自主行为候选，同时受锁定态约束（不变量 I2）。"""
        assert {BehaviorState.RUNNING, BehaviorState.STRETCHING} <= AUTONOMOUS_TARGETS

    def should_have_whitelist_entry_for_every_state(self):
        """新增状态漏配白名单时这里会红，而不是运行时抛错或静默卡死。"""
        assert set(ALLOWED_TRANSITIONS) == set(BehaviorState)

    def should_always_offer_a_way_back_to_idle(self):
        """任何状态都得能回待机，否则宠物会卡在某个动作里出不来。"""
        for src, targets in ALLOWED_TRANSITIONS.items():
            if src is BehaviorState.IDLE:
                continue
            assert BehaviorState.IDLE in targets, src

    def should_keep_sad_reachable_from_every_new_state(self):
        """心情崩了要能立刻拉脸：新状态也得列入 SAD 可进入集合。"""
        for src in (BehaviorState.RUNNING, BehaviorState.STRETCHING):
            assert BehaviorState.SAD in ALLOWED_TRANSITIONS[src]
