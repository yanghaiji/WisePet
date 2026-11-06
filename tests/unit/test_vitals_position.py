"""值对象测试：VitalsVO 钳制/衰减/恢复，PositionVO 边界钳制，gravity_step 贴地重力。"""
import pytest

from WisePet.domain.position import PositionVO, gravity_step
from WisePet.domain.vitals import DecayRules, VitalsVO

BOUNDS = (0, 0, 1920, 1080)
FLOOR_Y = 900


class TestVitalsVO:
    def should_clamp_values_into_range_when_construct_out_of_range(self):
        vitals = VitalsVO(hunger=150, mood=-20, energy=1000)
        assert vitals.hunger == 100
        assert vitals.mood == 0
        assert vitals.energy == 100

    def should_decay_hunger_linearly_when_time_passes(self):
        vitals = VitalsVO(hunger=100, mood=100, energy=100)
        rules = DecayRules(hunger_per_hour=4.0, mood_per_hour=0.0, energy_per_hour=0.0)
        after = vitals.decayed(dt_hours=5.0, rules=rules, is_night=False)
        assert after.hunger == pytest.approx(80.0)

    def should_decay_energy_faster_when_night(self):
        vitals = VitalsVO(hunger=100, mood=100, energy=100)
        rules = DecayRules(hunger_per_hour=0.0, mood_per_hour=0.0,
                           energy_per_hour=2.0, energy_night_factor=2.0)
        day = vitals.decayed(dt_hours=3.0, rules=rules, is_night=False)
        night = vitals.decayed(dt_hours=3.0, rules=rules, is_night=True)
        assert day.energy == pytest.approx(94.0)
        assert night.energy == pytest.approx(88.0)

    def should_restore_energy_when_sleeping(self):
        """睡一夜回血（PRD v1.7 C9）：精力往上走，饱食与心情照旧掉——睡觉不吃饭。"""
        vitals = VitalsVO(hunger=80, mood=80, energy=20)
        rules = DecayRules(hunger_per_hour=4.0, mood_per_hour=2.0,
                           energy_per_hour=3.0, energy_recover_per_hour=8.0)

        after = vitals.decayed(dt_hours=8.0, rules=rules, is_night=True, is_sleeping=True)

        assert after.energy == pytest.approx(84.0)     # 20 + 8×8，不再是越睡越困
        assert after.hunger == pytest.approx(48.0)
        assert after.mood == pytest.approx(64.0)

    def should_not_recover_beyond_the_cap_when_sleeping_forever(self):
        """回血受 100 上限钳住：睡再久也攒不出「精力溢出」的猫。"""
        vitals = VitalsVO(hunger=80, mood=80, energy=96)

        after = vitals.decayed(dt_hours=8.0, rules=DecayRules(), is_night=True,
                               is_sleeping=True)

        assert after.energy == 100.0

    def should_still_drain_energy_when_awake_at_night(self):
        """熬夜醒着照旧双倍掉：回血只属于睡着，否则「熬夜等回血」又变成睡觉。"""
        vitals = VitalsVO(hunger=100, mood=100, energy=100)
        rules = DecayRules(hunger_per_hour=0.0, mood_per_hour=0.0,
                           energy_per_hour=3.0, energy_night_factor=2.0)

        after = vitals.decayed(dt_hours=3.0, rules=rules, is_night=True, is_sleeping=False)

        assert after.energy == pytest.approx(82.0)

    def should_not_go_below_floor_when_long_time_decay(self):
        vitals = VitalsVO(hunger=50, mood=50, energy=50)
        rules = DecayRules(floor=10.0)
        after = vitals.decayed(dt_hours=1000.0, rules=rules, is_night=True)
        assert after.hunger == 10.0
        assert after.mood == 10.0
        assert after.energy == 10.0

    def should_keep_values_when_decay_hours_not_positive(self):
        vitals = VitalsVO(hunger=50, mood=50, energy=50)
        assert vitals.decayed(dt_hours=-1.0, rules=DecayRules(), is_night=False) == vitals
        assert vitals.decayed(dt_hours=0.0, rules=DecayRules(), is_night=False) == vitals

    def should_raise_hunger_when_fed(self):
        vitals = VitalsVO(hunger=90, mood=50, energy=50)
        after = vitals.fed(amount=20.0)
        assert after.hunger == 100  # 钳制上限

    def should_raise_mood_when_petted(self):
        vitals = VitalsVO(hunger=50, mood=50, energy=50)
        assert vitals.petted(amount=10.0).mood == 60

    def should_be_equal_by_value_when_fields_same(self):
        assert VitalsVO(1, 2, 3) == VitalsVO(1, 2, 3)


class TestPositionVO:
    def should_keep_position_when_inside_bounds(self):
        pos = PositionVO(x=100, y=200).clamped_to(BOUNDS)
        assert (pos.x, pos.y) == (100, 200)

    def should_clamp_to_edge_when_out_of_bounds(self):
        pos = PositionVO(x=9999, y=-50).clamped_to(BOUNDS)
        assert pos.x == 1920
        assert pos.y == 0

    def should_move_by_delta_when_shifted(self):
        pos = PositionVO(x=10, y=10).shifted(dx=5, dy=-3)
        assert (pos.x, pos.y) == (15, 7)


class TestGravityStep:
    """贴地重力纯函数（PRD v2.0 C10）：domain 不持时钟，dt 由 app 层传入。"""

    def should_fall_by_speed_times_dt_when_above_floor(self):
        y, landed = gravity_step(pos_y=100, floor_y=FLOOR_Y, dt=0.1, fall_speed_px_per_s=900.0)

        assert (y, landed) == (190, False)

    def should_report_landed_when_step_exactly_reaches_floor(self):
        y, landed = gravity_step(pos_y=800, floor_y=FLOOR_Y, dt=0.1, fall_speed_px_per_s=1000.0)

        assert (y, landed) == (FLOOR_Y, True)

    def should_converge_to_floor_when_step_overshoots(self):
        """过冲直接钳到地面，不反弹：否则低帧率下会在地面线上下抖动。"""
        y, landed = gravity_step(pos_y=880, floor_y=FLOOR_Y, dt=1.0, fall_speed_px_per_s=900.0)

        assert (y, landed) == (FLOOR_Y, True)

    def should_stay_put_when_already_on_floor(self):
        y, landed = gravity_step(pos_y=FLOOR_Y, floor_y=FLOOR_Y, dt=1.0, fall_speed_px_per_s=900.0)

        assert (y, landed) == (FLOOR_Y, False)

    def should_not_lift_when_snapshot_y_is_below_floor(self):
        """工作区变小/旧快照低于地面时不往上顶，静默停在原位（由启动重钳处理）。"""
        y, landed = gravity_step(pos_y=FLOOR_Y + 60, floor_y=FLOOR_Y,
                                 dt=1.0, fall_speed_px_per_s=900.0)

        assert (y, landed) == (FLOOR_Y + 60, False)

    @pytest.mark.parametrize("dt", [0.0, -1.0])
    def should_not_move_when_dt_not_positive(self, dt):
        y, landed = gravity_step(pos_y=100, floor_y=FLOOR_Y, dt=dt, fall_speed_px_per_s=900.0)

        assert (y, landed) == (100, False)

    def should_report_landing_once_when_falling_to_rest(self):
        """落地只报一次：后续 tick 不得反复报 landed，否则微弹会一直抖。"""
        y = 0
        landings = []
        for _ in range(6):
            y, landed = gravity_step(pos_y=y, floor_y=FLOOR_Y, dt=0.2,
                                     fall_speed_px_per_s=900.0)
            landings.append(landed)

        assert y == FLOOR_Y
        assert landings.count(True) == 1
