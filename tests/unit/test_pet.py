"""Pet 聚合根测试：行为方法、拖拽、时间衰减结算（不变量 I3/I4）。"""
import pytest

from WisePet.domain.errors import IllegalTransitionError
from WisePet.domain.pet import Pet
from WisePet.domain.position import PositionVO
from WisePet.domain.state_machine import BehaviorState
from WisePet.domain.vitals import DecayRules, VitalsVO

BOUNDS = (0, 0, 1920, 1080)


@pytest.fixture()
def pet():
    return Pet(pet_id="mochi", name="小墨",
               vitals=VitalsVO(hunger=80, mood=80, energy=80),
               position=PositionVO(x=100, y=100))


class TestPetBehaviors:
    def should_enter_hurt_and_lower_mood_when_clicked(self, pet):
        pet.hurt()
        assert pet.state is BehaviorState.HURT
        assert pet.vitals.mood == 75

    def should_back_to_idle_when_reaction_finished(self, pet):
        pet.hurt()
        pet.finish_action()
        assert pet.state is BehaviorState.IDLE

    def should_raise_hunger_and_eat_when_fed(self, pet):
        pet.feed()
        assert pet.state is BehaviorState.EATING
        assert pet.vitals.hunger == 100  # 80+20 钳制上限

    def should_raise_mood_when_head_petted(self, pet):
        pet.pet_head()
        assert pet.vitals.mood == 90
        assert pet.state is BehaviorState.IDLE  # 抚摸不切换行为

    def should_reject_feed_when_walking(self, pet):
        pet.start_walk()
        with pytest.raises(IllegalTransitionError):
            pet.feed()


class TestPetDragging:
    def should_lock_autonomy_when_dragging(self, pet):
        pet.start_drag()
        assert pet.autonomy_locked is True
        assert pet.can_autonomous() is False

    def should_move_by_delta_when_dragged(self, pet):
        pet.start_drag()
        pet.drag_by(dx=50, dy=30)
        assert (pet.position.x, pet.position.y) == (150, 130)

    def should_clamp_to_bounds_when_released_outside(self, pet):
        pet.start_drag()
        pet.drag_by(dx=99999, dy=-99999)
        pet.release_drag(bounds=BOUNDS)
        assert pet.position.x == 1920
        assert pet.position.y == 0
        assert pet.state is BehaviorState.IDLE

    def should_unlock_autonomy_when_released(self, pet):
        pet.start_drag()
        pet.release_drag(bounds=BOUNDS)
        assert pet.autonomy_locked is False
        assert pet.can_autonomous() is True


class TestPetDecay:
    def should_set_last_updated_on_first_settle_and_not_decay(self, pet):
        pet.settle_decay(now=1000.0, rules=DecayRules(), is_night=False)
        assert pet.last_updated == 1000.0
        assert pet.vitals == VitalsVO(hunger=80, mood=80, energy=80)

    def should_apply_decay_when_time_passed_since_last_settle(self, pet):
        pet.settle_decay(now=0.0, rules=DecayRules(), is_night=False)
        rules = DecayRules(hunger_per_hour=4.0, mood_per_hour=0.0, energy_per_hour=0.0)
        pet.settle_decay(now=5 * 3600.0, rules=rules, is_night=False)
        assert pet.vitals.hunger == pytest.approx(60.0)

    def should_restore_energy_while_sleeping(self, pet):
        """聚合根入口：当前状态是 SLEEPING 时补算走回血而不是衰减（PRD v1.7 C9）。"""
        pet._vitals = VitalsVO(hunger=80, mood=80, energy=30)
        pet.settle_decay(now=0.0, rules=DecayRules(), is_night=True)
        pet.transition(BehaviorState.SLEEPING, autonomous=True)

        pet.settle_decay(now=8 * 3600.0, rules=DecayRules(), is_night=True)

        assert pet.vitals.energy == pytest.approx(30.0 + DecayRules().energy_recover_per_hour * 8)
        assert pet.vitals.hunger < 80.0            # 饱食仍照正常衰减：睡着≠不饿

    def should_keep_values_when_clock_rolled_back(self, pet):
        """不变量 I4：系统时间回拨不衰减也不恢复。"""
        pet.settle_decay(now=10000.0, rules=DecayRules(), is_night=False)
        before = pet.vitals
        pet.settle_decay(now=5000.0, rules=DecayRules(hunger_per_hour=4.0), is_night=False)
        assert pet.vitals == before
        assert pet.last_updated == 10000.0  # 保留较新的时间戳

    def should_transmit_to_sad_when_mood_below_threshold(self, pet):
        pet._vitals = VitalsVO(hunger=50, mood=20, energy=50)
        assert pet.is_feeling_sad(sad_threshold=25) is True
        pet._vitals = VitalsVO(hunger=50, mood=25, energy=50)
        assert pet.is_feeling_sad(sad_threshold=25) is False

    def should_expose_vitals_summary_for_persona(self, pet):
        summary = pet.vitals_summary()
        assert "小墨" in summary
        assert "hunger" in summary or "饱食" in summary
