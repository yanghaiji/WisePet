"""动画节拍契约：帧数、逐动作帧率、一次性动作按整圈收场（PRD v2.0 C11 / US-1）。

这些数值原本只写在 `ui/frame_animator.py` 里，`app` 层要按它们安排收场时长就得反向
依赖 ui（依赖方向 ui → app → domain），本版提到 `domain.actions` 作单一真相源。
"""
import pytest

from WisePet.app.pet_controller import ONE_SHOT_DURATION_S, ONE_SHOT_MIN_DURATION_S
from WisePet.domain.actions import (
    ACTION_NAMES,
    FPS_BY_ACTION,
    FPS_FALLBACK,
    FRAMES_PER_ACTION,
    STATE_TO_ACTION,
    cycle_seconds,
    frame_interval_ms,
    whole_cycle_duration_s,
)


class TestFrameRateTable:
    def should_cover_every_action_when_table_built(self):
        """漏配一个动作就静默回落到兜底 12FPS，观感对了但没人知道为什么。"""
        assert set(FPS_BY_ACTION) == set(ACTION_NAMES)

    def should_fall_back_to_global_fps_when_action_unknown(self):
        assert frame_interval_ms("gallop") == round(1000 / FPS_FALLBACK)

    def should_be_in_a_human_readable_band_when_table_tuned(self):
        """4~16FPS：低于 4 时每帧超过 250ms，6 帧循环看着是换图而不是动画；
        高于 16 只是白烧 CPU（PRD NFR：空闲 CPU <3%）。"""
        assert all(4 <= fps <= 16 for fps in FPS_BY_ACTION.values())

    @pytest.mark.parametrize("action,slower_than", [
        ("sleep", "idle"),      # 睡着比醒着慢
        ("sad", "idle"),        # 低落比待机还静：同频就分不出情绪（实测帧间变化 1.7%）
    ])
    def should_read_differently_from_idle_when_action_is_calmer(self, action, slower_than):
        assert FPS_BY_ACTION[action] < FPS_BY_ACTION[slower_than]

    @pytest.mark.parametrize("action", ["run", "hurt"])
    @pytest.mark.parametrize("other", ["walk", "idle"])
    def should_beat_faster_when_motion_swings_wider(self, action, other):
        """帧间剪影跳变 36~39% 的动作必须比缓动作快播，否则大跳变读成抽搐。"""
        assert FPS_BY_ACTION[action] > FPS_BY_ACTION[other]

    def should_keep_walk_cycle_within_one_second_when_locomoting(self):
        """走路一圈不许超过 1 秒：6 帧循环再慢就像原地挪步（PRD v2.0 C11 满地走）。"""
        assert cycle_seconds("walk") <= 1.0


class TestCycleSeconds:
    def should_be_frames_over_fps_when_frame_count_is_contract(self):
        assert cycle_seconds("eat") == FRAMES_PER_ACTION / FPS_BY_ACTION["eat"]

    def should_honor_actual_frame_count_when_assets_differ(self):
        assert cycle_seconds("eat", frames=12) == 2 * cycle_seconds("eat")


class TestWholeCycleDuration:
    def should_round_up_to_whole_cycles_when_duration_lands_mid_loop(self):
        """落在循环中间收场＝表情被从中间剪断，必须补齐到整圈。"""
        for state, duration in ONE_SHOT_DURATION_S.items():
            cycle = cycle_seconds(STATE_TO_ACTION[state])
            assert duration / cycle == pytest.approx(round(duration / cycle))
            assert duration >= ONE_SHOT_MIN_DURATION_S[state]
            assert duration - cycle < ONE_SHOT_MIN_DURATION_S[state]

    def should_keep_the_shortest_cycle_when_minimum_is_below_one_loop(self):
        assert whole_cycle_duration_s("stretch", 0.7) == cycle_seconds("stretch")

    def should_be_one_cycle_when_action_slower_than_minimum(self):
        """睡眠一圈 1.5 秒：任何低于一圈的下限都只可能播满一圈。"""
        assert whole_cycle_duration_s("sleep", 1.0) == cycle_seconds("sleep")
