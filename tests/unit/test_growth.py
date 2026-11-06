"""成长体系领域模型测试：积分→等级→形态的纯函数与快照。"""
from WisePet.domain.growth import (
    ADULT_LEVEL,
    LEVEL_BASE_COST,
    STAGE_ADULT,
    STAGE_BABY,
    STAGE_TEEN,
    TEEN_LEVEL,
    GrowthVO,
    level_from_points,
    stage_for_level,
)


class TestLevel:
    def should_be_level_one_at_zero(self):
        assert level_from_points(0) == 1

    def should_reach_level_two_at_first_threshold(self):
        assert level_from_points(LEVEL_BASE_COST - 1) == 1
        assert level_from_points(LEVEL_BASE_COST) == 2

    def should_be_monotonic(self):
        assert level_from_points(20) == 2
        assert level_from_points(20 + 40) == 3          # 下一级门槛递增
        assert level_from_points(-5) == 1               # 负值当 0

    def should_match_cumulative_formula(self):
        total = sum(LEVEL_BASE_COST * lv for lv in range(1, 6))  # 升满 1~5 级
        assert level_from_points(total) == 6
        assert level_from_points(total - 1) == 5


class TestStage:
    def should_map_baby_teen_adult(self):
        assert stage_for_level(1) == STAGE_BABY
        assert stage_for_level(TEEN_LEVEL) == STAGE_TEEN
        assert stage_for_level(ADULT_LEVEL) == STAGE_ADULT


class TestSnapshot:
    def should_report_intra_level_progress(self):
        vo = GrowthVO.from_points(25, companion_days=3)   # 20 升满一级，剩 5
        assert vo.level == 2
        assert vo.level_points == 5
        assert vo.level_need == LEVEL_BASE_COST * 2
        assert vo.stage == STAGE_BABY
        assert vo.companion_days == 3
        assert 0.0 <= vo.stage_progress < 1.0

    def should_be_full_at_zero_need(self):
        assert GrowthVO.from_points(0).stage_progress == 0.0

    def should_floor_negative_days(self):
        assert GrowthVO.from_points(0, companion_days=-2).companion_days == 0
