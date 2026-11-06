"""PetController 编排测试：自主行为、拖拽、心情联动、求助冷却、贴地重力（PRD US-1/2/4 / v2.0 C10-C11）。"""
import logging

import pytest

from WisePet.app.pet_controller import (
    LAND_BOUNCE_PX,
    ONE_SHOT_DURATION_S,
    PetController,
)
from WisePet.domain.config_model import AppConfig
from WisePet.domain.events import (
    BehaviorChangedEvent,
    HelpRequestEvent,
    SoftJumpRequestedEvent,
    SpeakRequestedEvent,
    VitalsChangedEvent,
)
from WisePet.domain.pet import Pet
from WisePet.domain.position import Facing, PositionVO
from WisePet.domain.state_machine import BehaviorState
from WisePet.domain.vitals import VitalsVO
from fakes import FakeClock, StubRng, make_collecting_bus

BOUNDS = (0, 0, 1000, 600)
FLOOR_Y = BOUNDS[3]


def make_ctx(*, idle_s: float = 1e9, run_p: float = 0.0, stretch_p: float = 0.0,
             rng=None, position: PositionVO | None = None, bounds=BOUNDS,
             vitals: VitalsVO | None = None, roam: bool = False) -> dict:
    """统一装配控制器。

    闲置间隔默认推到 1e9：`_next_action_at` 在构造时就算好了，测试中途再改
    `cfg.pet.idle_min_s` 是无效的——旧用例 should_help_again_after_cooldown 就是这么
    靠随机数调用顺序侥幸通过的。要测自主行为就显式传 idle_s。

    小跑/伸懒腰的概率默认关掉：基础夹具保持 v1.x 的“只走”，概率分支由专门用例打开。
    自由漫游（roam）默认关闭：存量用例按贴地语义断言，漫游行为由专门用例覆盖。
    """
    cfg = AppConfig()
    cfg.vitals.night_start_hour = 0
    cfg.vitals.night_end_hour = 0          # 关闭夜间窗口（start==end → 恒非夜间），排除机器时区干扰
    cfg.pet.idle_min_s = cfg.pet.idle_max_s = idle_s
    cfg.pet.run_trigger_p = run_p
    cfg.pet.stretch_trigger_p = stretch_p
    cfg.pet.roam = roam
    cfg.vitals.save_interval_s = 1e9
    clock = FakeClock()
    bus, collected = make_collecting_bus()
    pet = Pet(pet_id="mochi", name="小墨",
              vitals=vitals or VitalsVO(),
              position=position or PositionVO(500, FLOOR_Y))
    kwargs = {} if bounds is None else {"bounds_provider": lambda: bounds}
    controller = PetController(pet, bus, lambda: cfg, clock,
                               rng=rng or StubRng(), **kwargs)
    return {"cfg": cfg, "clock": clock, "bus": bus, "events": collected,
            "pet": pet, "controller": controller}


@pytest.fixture()
def ctx():
    return make_ctx()


class TestFreePlacement:
    """关掉贴地后：用户拖到哪就停在哪，且照常走动（PRD v2.0 C10 的可关开关）。"""

    def should_keep_dropped_position_when_stick_to_floor_off(self):
        ctx = make_ctx(position=PositionVO(500, 100))
        ctx["cfg"].pet.stick_to_floor = False
        ctx["controller"].on_press()
        ctx["controller"].on_drag(30, -40)
        ctx["controller"].on_release()
        dropped = ctx["pet"].position

        ctx["clock"].advance(1.0)
        ctx["controller"].tick()

        assert ctx["pet"].position == dropped

    def should_walk_at_custom_height_when_stick_to_floor_off(self):
        ctx = make_ctx(idle_s=2.0, position=PositionVO(500, 100))
        ctx["cfg"].pet.stick_to_floor = False
        ctx["clock"].advance(3.0)
        ctx["controller"].tick()          # 进入 WALKING，此拍 _last_tick 尚未就位
        before = ctx["pet"].position.x
        ctx["clock"].advance(1.0)
        ctx["controller"].tick()

        assert ctx["pet"].position.x != before
        assert ctx["pet"].position.y == 100


class TestRoam:
    """自由漫游（PRD 用户反馈 3）：不贴地、任意高度走/睡、定时柔和瞬移换地方。"""

    def should_not_fall_when_roam_on_even_with_stick_to_floor(self):
        ctx = make_ctx(position=PositionVO(500, 100), roam=True)
        ctx["clock"].advance(1.0)
        ctx["controller"].tick()

        assert ctx["pet"].position == PositionVO(500, 100)   # 没有重力拽回地面

    def should_walk_at_height_when_roam_on(self):
        ctx = make_ctx(idle_s=2.0, position=PositionVO(500, 300), roam=True)
        ctx["clock"].advance(3.0)
        ctx["controller"].tick()
        ctx["clock"].advance(1.0)
        ctx["controller"].tick()

        assert ctx["pet"].position.y == 300                  # 高度不变，水平照走
        assert ctx["pet"].state is BehaviorState.WALKING

    def _trigger_jump(self, ctx):
        """首拍武装计时，再推过间隔触发瞬移；返回瞬移事件。"""
        ctx["controller"].tick()
        ctx["clock"].advance(31.0)
        ctx["controller"].tick()
        return next(e for e in ctx["events"] if isinstance(e, SoftJumpRequestedEvent))

    def should_publish_soft_jump_when_interval_elapsed(self):
        ctx = make_ctx(idle_s=1e9, roam=True)
        ctx["cfg"].pet.roam_jump_interval_s = 30.0

        jump = self._trigger_jump(ctx)

        assert 0 <= jump.x <= BOUNDS[2] and 0 <= jump.y <= BOUNDS[3]
        assert ctx["controller"].is_moving() is False        # 瞬移期间冻结位移

    def should_not_jump_when_interval_zero(self):
        ctx = make_ctx(idle_s=1e9, roam=True)
        ctx["cfg"].pet.roam_jump_interval_s = 0.0
        ctx["controller"].tick()
        ctx["clock"].advance(120.0)
        ctx["controller"].tick()

        assert [e for e in ctx["events"]
                if isinstance(e, SoftJumpRequestedEvent)] == []

    def should_land_on_target_after_finish_soft_jump(self):
        ctx = make_ctx(idle_s=1e9, roam=True)
        jump = self._trigger_jump(ctx)

        ctx["controller"].move_to(jump.x, jump.y)            # ui 落位
        ctx["controller"].finish_soft_jump()                 # 淡入完成回调

        assert ctx["pet"].position == PositionVO(jump.x, jump.y)
        assert ctx["controller"]._soft_jump_active is False

    def should_abort_jump_when_user_grabs_pet(self):
        ctx = make_ctx(idle_s=1e9, roam=True)
        self._trigger_jump(ctx)
        assert ctx["controller"]._soft_jump_active is True

        ctx["controller"].on_press()                          # 用户中途抓住

        assert ctx["controller"]._soft_jump_active is False
        assert ctx["pet"].state is BehaviorState.DRAGGING


class TestAutonomousBehavior:
    def should_start_walking_when_idle_gap_elapsed(self):
        ctx = make_ctx(idle_s=2.0)
        ctx["clock"].advance(3.0)
        ctx["controller"].tick()
        assert ctx["pet"].state is BehaviorState.WALKING
        assert BehaviorChangedEvent in [type(e) for e in ctx["events"]]

    def should_move_position_when_walking_ticks(self):
        ctx = make_ctx(idle_s=2.0, position=PositionVO(500, FLOOR_Y))
        ctx["clock"].advance(3.0)
        ctx["controller"].tick()  # 进入 WALKING
        before = ctx["pet"].position.x
        ctx["clock"].advance(1.0)
        ctx["controller"].tick()
        assert ctx["pet"].position.x != before

    def should_turn_back_and_keep_walking_at_edge(self, ctx):
        """到边折返不再立刻停下：旧行为“撞边就站住”让宠物永远走不出溜达的样子（v2.0 C11）。"""
        ctx["pet"].position = PositionVO(995, FLOOR_Y)
        ctx["pet"].start_walk()  # 直接进走动，初始方向 +1（向右）
        ctx["controller"].tick()  # 建立 last_tick
        ctx["clock"].advance(2.0)
        ctx["controller"].tick()
        assert ctx["pet"].position.x == BOUNDS[2]
        assert ctx["pet"].facing is Facing.LEFT
        assert ctx["pet"].state is BehaviorState.WALKING

    def should_end_trip_at_edge_after_walk_max_s(self):
        """走满 walk_max_s 后到边才收趟（起步走的是控制器，才会记上下趟开始时刻）。"""
        ctx = make_ctx(idle_s=2.0, position=PositionVO(5, FLOOR_Y))
        ctx["cfg"].pet.walk_max_s = 1.0
        ctx["clock"].advance(3.0)
        ctx["controller"].tick()              # → WALKING，方向 -1（向左）
        assert ctx["pet"].state is BehaviorState.WALKING
        ctx["clock"].advance(2.0)
        ctx["controller"].tick()              # 到左边且 elapsed=2.0 ≥ walk_max_s → 收趟
        assert ctx["pet"].state is BehaviorState.IDLE

    def should_end_trip_at_edge_when_random_wants_to_stop(self):
        """没走满也可能被随机数喊停：不然光靠 walk_max_s 会走得太机械（v2.0 C11）。"""
        ctx = make_ctx(idle_s=2.0, position=PositionVO(5, FLOOR_Y),
                       rng=StubRng(values=[0.99, 0.99, 0.1]))   # 不懒腰、不小跑、到边想停
        ctx["cfg"].pet.walk_max_s = 100.0
        ctx["clock"].advance(3.0)
        ctx["controller"].tick()              # → WALKING，方向 -1（向左）
        ctx["clock"].advance(0.5)
        ctx["controller"].tick()              # 到边但远未到 walk_max → 由随机数说了算
        assert ctx["pet"].state is BehaviorState.IDLE

    def should_keep_walking_after_turning_back_when_random_wants_to_go_on(self):
        """到边折返后随机数说“还想走”就继续走，而不是每撞到边都要停一下。"""
        ctx = make_ctx(idle_s=2.0, position=PositionVO(5, FLOOR_Y),
                       rng=StubRng(values=[0.99, 0.99]))        # 末尾回落到 0.99 ≥ 0.35
        ctx["cfg"].pet.walk_max_s = 100.0
        ctx["clock"].advance(3.0)
        ctx["controller"].tick()              # → WALKING，向左
        ctx["clock"].advance(0.5)
        ctx["controller"].tick()              # 撞左边 → 折返继续走

        assert ctx["pet"].state is BehaviorState.WALKING
        assert ctx["pet"].facing is Facing.RIGHT
        ctx["clock"].advance(1.0)
        ctx["controller"].tick()
        assert ctx["pet"].position.x > 0      # 脚步已经往右走开了

    def should_enter_sad_when_mood_below_threshold_on_tick(self, ctx):
        ctx["pet"]._vitals = VitalsVO(hunger=50, mood=10, energy=50)
        ctx["controller"].tick()
        assert ctx["pet"].state is BehaviorState.SAD

    def should_stroll_from_sad_when_idle_gap_elapsed(self):
        """难过不等于瘫痪：没人投喂时它也得偶尔起身走两步，不能永远卡在 SAD。"""
        ctx = make_ctx(idle_s=2.0, vitals=VitalsVO(hunger=50, mood=10, energy=50))
        ctx["clock"].advance(3.0)
        ctx["controller"].tick()
        assert ctx["pet"].state is BehaviorState.WALKING

    def should_not_yank_walking_sad_pet_back_to_sad(self):
        """难过的猫走在一趟中途时，不能被下一 tick 的 _maybe_sad 拽回去（否则走一步停一步）。"""
        ctx = make_ctx(idle_s=2.0, vitals=VitalsVO(hunger=50, mood=10, energy=50))
        ctx["clock"].advance(3.0)
        ctx["controller"].tick()                    # → WALKING（从 SAD 起身）
        assert ctx["pet"].state is BehaviorState.WALKING
        ctx["clock"].advance(0.5)                   # 未到边、未走满
        ctx["controller"].tick()
        assert ctx["pet"].state is BehaviorState.WALKING


class TestPollingVisibility:
    """真机反馈「看不到其它状态」的回归闸：轮询节奏、收趟排期、状态可观测。"""

    def should_rest_before_next_stroll_when_trip_ends(self):
        ctx = make_ctx(idle_s=2.0, position=PositionVO(500, FLOOR_Y))
        ctx["cfg"].pet.walk_max_s = 1.0
        ctx["clock"].advance(3.0)
        ctx["controller"].tick()               # → WALKING
        ctx["clock"].advance(2.0)
        ctx["controller"].tick()               # 走满 → IDLE，并重新排空闲间隔
        assert ctx["pet"].state is BehaviorState.IDLE

        ctx["clock"].advance(1.0)
        ctx["controller"].tick()               # 空闲间隔还没到，不该立刻又出发

        assert ctx["pet"].state is BehaviorState.IDLE

    def should_end_trip_when_walk_max_elapsed_without_touching_edge(self):
        ctx = make_ctx(idle_s=2.0, position=PositionVO(500, FLOOR_Y))
        ctx["cfg"].pet.walk_max_s = 1.0
        ctx["clock"].advance(3.0)
        ctx["controller"].tick()
        assert ctx["pet"].state is BehaviorState.WALKING

        ctx["clock"].advance(2.0)
        ctx["controller"].tick()               # 中途没撞边，但走满了就该收趟

        assert 0 < ctx["pet"].position.x < BOUNDS[2]
        assert ctx["pet"].state is BehaviorState.IDLE

    def should_walk_inward_when_stroll_starts_at_left_edge(self):
        ctx = make_ctx(idle_s=2.0, position=PositionVO(0, FLOOR_Y))
        ctx["clock"].advance(3.0)
        ctx["controller"].tick()

        assert ctx["pet"].state is BehaviorState.WALKING
        assert ctx["pet"].facing is Facing.RIGHT

    def should_log_behavior_changes_at_info(self, ctx, caplog):
        with caplog.at_level(logging.INFO, logger="WisePet.app.pet_controller"):
            ctx["controller"].feed()

        assert "IDLE -> EATING" in caplog.text

    def should_hold_stretch_long_enough_to_be_seen(self):
        assert ONE_SHOT_DURATION_S[BehaviorState.STRETCHING] >= 1.5

    def should_not_reenter_sad_immediately_when_mood_stays_low(self):
        """心情贴底时 SAD 也要有冷却：IDLE 一出现就被拉走，别的动作永远没机会。"""
        ctx = make_ctx(idle_s=1e9, vitals=VitalsVO(hunger=50, mood=10, energy=50))
        ctx["controller"].tick()
        assert ctx["pet"].state is BehaviorState.SAD

        ctx["controller"].on_action_finished()        # SAD -> IDLE
        ctx["clock"].advance(1.0)
        ctx["controller"].tick()

        assert ctx["pet"].state is BehaviorState.IDLE

    def should_still_allow_sleep_from_sad_at_night_when_energy_bottomed(self):
        """低心情不该把睡觉锁死：睡着才回精力，否则数值永远卡在 floor。"""
        ctx = make_ctx(idle_s=2.0, vitals=VitalsVO(hunger=50, mood=10, energy=10))
        ctx["cfg"].vitals.night_start_hour = 0
        ctx["cfg"].vitals.night_end_hour = 24
        ctx["clock"].advance(3.0)
        ctx["controller"].tick()

        assert ctx["pet"].state is BehaviorState.SLEEPING


class TestDragAndClick:
    def should_freeze_autonomy_while_dragging(self, ctx):
        ctx["controller"].on_press()
        ctx["clock"].advance(30.0)  # 远超闲置间隔
        ctx["controller"].tick()
        assert ctx["pet"].state is BehaviorState.DRAGGING

    def should_hurt_when_released_without_movement(self, ctx):
        ctx["controller"].on_press()
        ctx["controller"].on_release()
        assert ctx["pet"].state is BehaviorState.HURT
        assert ctx["pet"].vitals.mood < 80

    def should_clamp_position_when_released_outside_bounds(self, ctx):
        ctx["controller"].on_press()
        ctx["controller"].on_drag(5000, 5000)
        ctx["controller"].on_release()
        assert ctx["pet"].position.x == BOUNDS[2]
        assert ctx["pet"].position.y == BOUNDS[3]
        assert ctx["pet"].state is BehaviorState.IDLE

    def should_grab_pet_even_while_thinking(self, ctx):
        """拖拽是直接操纵：等 AI 回复那几秒也得能把它抓起来。"""
        ctx["pet"].start_thinking()
        ctx["controller"].on_press()
        assert ctx["pet"].state is BehaviorState.DRAGGING

    def should_grab_pet_while_sad(self, ctx):
        """心情长期低于阈值时它一直卡在 SAD，那时恰恰最需要被抱走。"""
        ctx["pet"]._vitals = VitalsVO(hunger=50, mood=10, energy=50)
        ctx["controller"].tick()
        assert ctx["pet"].state is BehaviorState.SAD
        ctx["controller"].on_press()
        assert ctx["pet"].state is BehaviorState.DRAGGING
        ctx["controller"].on_drag(40, 0)
        ctx["controller"].on_release()
        assert ctx["pet"].state is BehaviorState.IDLE


class TestInteraction:
    def should_eat_and_raise_hunger_when_fed(self, ctx):
        ctx["controller"].feed()
        assert ctx["pet"].state is BehaviorState.EATING
        assert ctx["pet"].vitals.hunger > 80
        assert VitalsChangedEvent in [type(e) for e in ctx["events"]]

    def should_leave_sad_and_eat_when_fed_in_sad(self, ctx):
        ctx["pet"]._vitals = VitalsVO(hunger=10, mood=10, energy=50)
        ctx["controller"].tick()  # → SAD
        ctx["controller"].feed()
        assert ctx["pet"].state is BehaviorState.EATING

    def should_raise_mood_without_state_change_when_petted(self, ctx):
        before = ctx["pet"].state
        ctx["controller"].pet_head()
        assert ctx["pet"].vitals.mood == 90
        assert ctx["pet"].state is before

    def should_finish_action_back_to_idle_when_animation_done(self, ctx):
        ctx["controller"].on_press()
        ctx["controller"].on_release()  # → HURT
        ctx["controller"].on_action_finished()
        assert ctx["pet"].state is BehaviorState.IDLE

    def should_feed_while_walking_interrupts_to_eating(self, ctx):
        """走/跑途中点投喂：先停步回待机再进食，而不是静默没反应。"""
        ctx["pet"].start_walk()
        ctx["controller"].feed()
        assert ctx["pet"].state is BehaviorState.EATING
        assert ctx["pet"].vitals.hunger > 80
        assert VitalsChangedEvent in [type(e) for e in ctx["events"]]

    def should_feed_while_hurt_interrupts_to_eating(self, ctx):
        """被戳的瞬间点投喂也能打断，重新开吃。"""
        ctx["controller"].on_press()
        ctx["controller"].on_release()  # → HURT
        ctx["controller"].feed()
        assert ctx["pet"].state is BehaviorState.EATING

    def should_not_feed_while_thinking(self, ctx):
        """等 AI 回复时投喂不动：手在等回复，不该把思考打断成吃东西。"""
        ctx["pet"].start_thinking()
        ctx["controller"].feed()
        assert ctx["pet"].state is BehaviorState.THINKING

    def should_speak_happy_line_when_petted(self, ctx):
        """抚摸要有可见反馈：冒一句开心气泡（source=pet）。"""
        ctx["controller"].pet_head()
        happy = [e for e in ctx["events"] if isinstance(e, SpeakRequestedEvent)]
        assert happy and happy[0].source == "pet"
        assert ctx["pet"].vitals.mood > 80

    def should_not_speak_when_petted_while_thinking(self, ctx):
        """AI 对话中不抢话：抚摸只抬心情，不冒气泡。"""
        ctx["pet"].start_thinking()
        ctx["controller"].pet_head()
        happy = [e for e in ctx["events"] if isinstance(e, SpeakRequestedEvent)]
        assert not happy
        assert ctx["pet"].vitals.mood > 80


class TestConversationFreeze:
    """FR v1.8 C14：聊天窗打开/思考/说话期间，宠物原地不动。"""

    def should_enter_thinking_from_walking_when_conversation_starts(self, ctx):
        ctx["pet"].start_walk()
        ctx["controller"].start_thinking()
        assert ctx["pet"].state is BehaviorState.THINKING

    def should_enter_thinking_from_hurt_when_conversation_starts(self, ctx):
        ctx["controller"].on_press()
        ctx["controller"].on_release()  # → HURT
        ctx["controller"].start_thinking()
        assert ctx["pet"].state is BehaviorState.THINKING

    def should_reenter_thinking_when_new_message_sent_while_speaking(self, ctx):
        ctx["pet"].start_thinking()
        ctx["pet"].start_speaking()
        ctx["controller"].start_thinking()
        assert ctx["pet"].state is BehaviorState.THINKING

    def should_not_interrupt_dragging_when_conversation_starts(self, ctx):
        ctx["controller"].on_press()
        ctx["controller"].start_thinking()
        assert ctx["pet"].state is BehaviorState.DRAGGING

    def should_freeze_position_during_conversation(self):
        ctx = make_ctx(position=PositionVO(500, 100))
        ctx["controller"].set_conversation_active(True)
        ctx["clock"].advance(1.0)
        ctx["controller"].tick()
        assert ctx["pet"].position.y == 100
        assert ctx["controller"].is_moving() is False

    def should_freeze_gravity_while_thinking_even_if_window_closed(self):
        ctx = make_ctx(position=PositionVO(500, 100))
        ctx["pet"].start_thinking()
        ctx["clock"].advance(1.0)
        ctx["controller"].tick()
        assert ctx["pet"].position.y == 100
        assert ctx["controller"].is_moving() is False

    def should_skip_autonomous_actions_while_chat_window_open(self):
        ctx = make_ctx(idle_s=0.0)
        ctx["controller"].set_conversation_active(True)
        ctx["clock"].advance(10.0)
        ctx["controller"].tick()
        assert ctx["pet"].state is BehaviorState.IDLE
        assert ctx["controller"].is_moving() is False

    def should_ignore_reminder_move_while_conversation_active(self, ctx):
        ctx["controller"].set_conversation_active(True)
        ctx["controller"].move_to(100, 100)
        assert ctx["pet"].position.x == 500

    def should_resume_after_chat_window_closed(self):
        ctx = make_ctx(idle_s=0.0, position=PositionVO(500, 100))
        ctx["controller"].set_conversation_active(True)
        ctx["controller"].tick()               # 建立时间基线（冻结中不下落）
        assert ctx["pet"].position.y == 100
        ctx["controller"].set_conversation_active(False)
        ctx["clock"].advance(1.0)
        ctx["controller"].tick()
        assert ctx["pet"].position.y > 100
        assert ctx["controller"].conversation_active is False


class TestHelpRequest:
    def should_speak_help_once_when_vitals_dangerous(self, ctx):
        ctx["pet"]._vitals = VitalsVO(hunger=5, mood=80, energy=80)
        ctx["controller"].tick()
        helps = [e for e in ctx["events"] if isinstance(e, HelpRequestEvent)]
        speaks = [e for e in ctx["events"] if isinstance(e, SpeakRequestedEvent)]
        assert len(helps) == 1 and helps[0].reason == "hunger"
        assert len(speaks) == 1

    def should_not_help_again_within_cooldown(self, ctx):
        ctx["pet"]._vitals = VitalsVO(hunger=5, mood=80, energy=80)
        ctx["controller"].tick()
        ctx["clock"].advance(ctx["cfg"].vitals.help_cooldown_s - 10)
        ctx["controller"].tick()
        assert len([e for e in ctx["events"] if isinstance(e, HelpRequestEvent)]) == 1

    def should_help_again_after_cooldown(self, ctx):
        ctx["pet"]._vitals = VitalsVO(hunger=5, mood=80, energy=80)
        ctx["controller"].tick()
        ctx["clock"].advance(ctx["cfg"].vitals.help_cooldown_s + 1)
        ctx["controller"].tick()
        assert len([e for e in ctx["events"] if isinstance(e, HelpRequestEvent)]) == 2


class TestReminderMove:
    def should_walk_to_target_x_and_stay_on_floor_when_move_to(self, ctx):
        """贴地模式下提醒位移只取 x：语义是“走过去提醒你”而不是“飞到鼠标旁”（v2.0 C10）。"""
        ctx["pet"].start_walk()
        ctx["controller"].move_to(300, 400)
        assert ctx["pet"].position == PositionVO(300, FLOOR_Y)
        assert ctx["pet"].state is BehaviorState.IDLE

    def should_keep_target_y_when_floor_sticking_disabled(self):
        ctx = make_ctx()
        ctx["cfg"].pet.stick_to_floor = False
        ctx["controller"].move_to(300, 400)
        assert ctx["pet"].position == PositionVO(300, 400)

    def should_stop_running_when_move_to(self, ctx):
        ctx["pet"].transition(BehaviorState.RUNNING, autonomous=True)
        ctx["controller"].move_to(300, 400)
        assert ctx["pet"].state is BehaviorState.IDLE

    def should_ignore_move_to_while_thinking(self, ctx):
        ctx["pet"].start_thinking()
        before = ctx["pet"].position
        ctx["controller"].move_to(300, 400)
        assert ctx["pet"].position is before


class TestCommandGuards:
    def should_ignore_release_when_never_pressed(self, ctx):
        ctx["controller"].on_release()
        assert ctx["pet"].state is BehaviorState.IDLE
        assert ctx["events"] == []

    def should_announce_walk_stop_before_hurt_when_clicked_while_walking(self, ctx):
        ctx["pet"].start_walk()
        ctx["events"].clear()

        ctx["controller"].on_click()

        assert ctx["pet"].state is BehaviorState.HURT
        announced = [e.new for e in ctx["events"] if isinstance(e, BehaviorChangedEvent)]
        assert announced == [BehaviorState.IDLE, BehaviorState.HURT]

    def should_swallow_illegal_autonomous_transition_while_dragging(self, ctx):
        """锁定态下自主迁移被拒：不变量 I2，不抛异常也不改状态。"""
        ctx["pet"]._vitals = VitalsVO(hunger=80, mood=10, energy=80)
        ctx["controller"].on_press()

        ctx["controller"].tick()

        assert ctx["pet"].state is BehaviorState.DRAGGING


class TestNightBehavior:
    def should_fall_asleep_when_night_and_energy_low(self):
        ctx = make_ctx(idle_s=2.0)
        cfg = ctx["cfg"]
        cfg.vitals.night_start_hour, cfg.vitals.night_end_hour = 0, 24  # 全天夜，脱离时区干扰
        ctx["pet"]._vitals = VitalsVO(hunger=80, mood=80, energy=10)
        ctx["clock"].advance(3.0)

        ctx["controller"].tick()

        assert ctx["pet"].state is BehaviorState.SLEEPING

    def should_wake_up_at_dawn_with_energy_restored(self):
        """睡一夜的结果必须是「有精神」，否则天亮第一件事又是喊累（PRD v1.7 C9）。"""
        ctx = make_ctx(idle_s=2.0)
        cfg = ctx["cfg"]
        cfg.vitals.night_start_hour, cfg.vitals.night_end_hour = 0, 24
        ctx["pet"]._vitals = VitalsVO(hunger=80, mood=80, energy=10)
        ctx["clock"].advance(3.0)
        ctx["controller"].tick()                    # → SLEEPING
        ctx["clock"].advance(8 * 3600.0)            # 睡满一夜
        ctx["controller"].tick()
        cfg.vitals.night_start_hour = cfg.vitals.night_end_hour = 0   # 天亮
        ctx["events"].clear()

        ctx["clock"].advance(3.0)
        ctx["controller"].tick()

        assert ctx["pet"].state is BehaviorState.IDLE
        assert ctx["pet"].vitals.energy > cfg.vitals.sad_threshold
        assert [e for e in ctx["events"] if isinstance(e, HelpRequestEvent)] == []

    def should_report_rested_not_decay_when_energy_came_up_while_sleeping(self):
        """事件要说清精力为什么涨了：睡着回血还报 `decay`，下游只能读成越睡越累。"""
        ctx = make_ctx(idle_s=2.0)
        cfg = ctx["cfg"]
        cfg.vitals.night_start_hour, cfg.vitals.night_end_hour = 0, 24
        ctx["pet"]._vitals = VitalsVO(hunger=80, mood=80, energy=10)
        ctx["clock"].advance(3.0)
        ctx["controller"].tick()                    # → SLEEPING
        ctx["events"].clear()

        ctx["clock"].advance(3600.0)
        ctx["controller"].tick()

        changed = [e for e in ctx["events"] if isinstance(e, VitalsChangedEvent)]
        assert [c.changed for c in changed] == [("rested",)]

    def should_wake_up_and_ask_for_energy_when_day_arrives(self):
        ctx = make_ctx(idle_s=2.0)
        cfg = ctx["cfg"]
        cfg.vitals.night_start_hour, cfg.vitals.night_end_hour = 0, 24
        ctx["pet"]._vitals = VitalsVO(hunger=80, mood=80, energy=10)
        ctx["clock"].advance(3.0)
        ctx["controller"].tick()          # → SLEEPING
        cfg.vitals.night_start_hour = cfg.vitals.night_end_hour = 0   # 天亮
        ctx["events"].clear()

        ctx["clock"].advance(3.0)
        ctx["controller"].tick()

        assert ctx["pet"].state is BehaviorState.IDLE
        helps = [e for e in ctx["events"] if isinstance(e, HelpRequestEvent)]
        assert [h.reason for h in helps] == ["energy"]   # 白天精力不足才求助（PRD US-4）

    def should_stretch_on_the_tick_after_waking_up(self):
        """睡醒下一 tick 伸个懒腰：本 tick 先把求助话说完，不把求助时序挤掉（v2.0 C11）。"""
        ctx = make_ctx(idle_s=2.0)
        cfg = ctx["cfg"]
        cfg.vitals.night_start_hour, cfg.vitals.night_end_hour = 0, 24
        ctx["pet"]._vitals = VitalsVO(hunger=80, mood=80, energy=10)
        ctx["clock"].advance(3.0)
        ctx["controller"].tick()                                    # → SLEEPING
        cfg.vitals.night_start_hour = cfg.vitals.night_end_hour = 0  # 天亮
        ctx["controller"].tick()                                    # → IDLE（先报求助）

        ctx["clock"].advance(1.0)
        ctx["controller"].tick()                                    # → STRETCHING

        assert ctx["pet"].state is BehaviorState.STRETCHING


class TestFloorAndGravity:
    """贴地行走与重力回落（PRD v2.0 C10）。"""

    def should_fall_back_to_floor_when_left_in_air(self):
        ctx = make_ctx(position=PositionVO(500, 100))
        ctx["controller"].tick()                     # 建立 last_tick（dt=0 不位移）
        assert ctx["pet"].position.y == 100
        ctx["clock"].advance(0.5)
        ctx["controller"].tick_motion()              # 0.5s × 900px/s = 450px
        assert ctx["pet"].position.y == 550
        ctx["clock"].advance(0.5)
        ctx["controller"].tick_motion()              # 过冲 → 收敛到地面（本 tick 微弹）
        assert ctx["pet"].position.y == FLOOR_Y - LAND_BOUNCE_PX
        ctx["clock"].advance(0.5)
        ctx["controller"].tick_motion()              # 微弹只一次，之后静立在地面
        assert ctx["pet"].position.y == FLOOR_Y

    def should_not_fall_while_dragging(self, ctx):
        ctx["pet"].position = PositionVO(500, 100)
        ctx["controller"].on_press()
        ctx["controller"].tick()
        ctx["clock"].advance(1.0)
        ctx["controller"].tick_motion()
        assert ctx["pet"].position.y == 100          # 用户手上不施重力

    def should_not_report_moving_while_user_holds_the_pet(self):
        """被抓在手上不算“在动”：运动计时器该停，否则拖拽时白跑 50ms 高频通道。"""
        ctx = make_ctx(position=PositionVO(500, 100))
        ctx["controller"].on_press()

        assert ctx["pet"].state is BehaviorState.DRAGGING
        assert ctx["controller"].is_moving() is False

    def should_stay_in_air_when_stick_to_floor_disabled(self):
        ctx = make_ctx(position=PositionVO(500, 100))
        ctx["cfg"].pet.stick_to_floor = False
        ctx["controller"].tick()
        ctx["clock"].advance(1.0)
        ctx["controller"].tick_motion()
        assert ctx["pet"].position.y == 100

    def should_not_fall_without_a_real_workarea_bounds(self):
        """没注入真实工作区（±1e9 哨兵）时不施重力：否则宠物会往无穷远掉。"""
        ctx = make_ctx(position=PositionVO(500, 100), bounds=None)
        ctx["controller"].tick()
        ctx["clock"].advance(1.0)
        ctx["controller"].tick_motion()
        assert ctx["pet"].position.y == 100
        assert ctx["controller"].is_moving() is False

    def should_rest_on_floor_and_not_jitter_after_landing(self):
        ctx = make_ctx(position=PositionVO(500, 100))
        for _ in range(6):
            ctx["clock"].advance(0.5)
            ctx["controller"].tick_motion()
        assert ctx["pet"].position.y == FLOOR_Y
        ctx["clock"].advance(0.5)
        ctx["controller"].tick_motion()
        assert ctx["pet"].position.y == FLOOR_Y      # 落地后不再报弹跳

    def should_report_moving_only_while_airborne_or_locomoting(self):
        ctx = make_ctx(position=PositionVO(500, FLOOR_Y))
        assert ctx["controller"].is_moving() is False   # 静立在地：不需要高频驱动
        ctx["pet"].position = PositionVO(500, 100)
        assert ctx["controller"].is_moving() is True    # 在空中
        ctx["pet"].position = PositionVO(500, FLOOR_Y)
        ctx["pet"].start_walk()
        assert ctx["controller"].is_moving() is True


class TestRunAndStretch:
    """小跑与伸懒腰（PRD v2.0 C11）。"""

    def should_start_running_when_run_probability_hits(self):
        ctx = make_ctx(idle_s=2.0, run_p=0.5, rng=StubRng(values=[0.99, 0.1]))
        ctx["clock"].advance(3.0)
        ctx["controller"].tick()                     # 伸懒腰不命中，小跑命中
        assert ctx["pet"].state is BehaviorState.RUNNING

    def should_move_at_run_speed_when_running(self):
        ctx = make_ctx(idle_s=2.0, run_p=0.5, rng=StubRng(values=[0.99, 0.1]),
                       position=PositionVO(500, FLOOR_Y))
        ctx["clock"].advance(3.0)
        ctx["controller"].tick()
        ctx["clock"].advance(1.0)
        ctx["controller"].tick_motion()
        # StubRng.choice 取首元 = -1（向左）
        assert ctx["pet"].position.x == 500 - round(ctx["cfg"].pet.run_speed_px_per_s)

    def should_downgrade_run_to_walk_when_tired_at_edge(self):
        """跑累了降回走而不是急停（v2.0 C11）。"""
        ctx = make_ctx(idle_s=2.0, run_p=0.5, rng=StubRng(values=[0.99, 0.1]),
                       position=PositionVO(5, FLOOR_Y))
        ctx["cfg"].pet.walk_max_s = 1.0
        ctx["clock"].advance(3.0)
        ctx["controller"].tick()                     # → RUNNING，向左
        ctx["clock"].advance(1.0)
        ctx["controller"].tick()                     # 到左边且 elapsed ≥ 0.6×walk_max_s
        assert ctx["pet"].state is BehaviorState.WALKING

    def should_stretch_instead_of_walking_when_stretch_probability_hits(self):
        ctx = make_ctx(idle_s=2.0, stretch_p=0.5, rng=StubRng(values=[0.1]))
        ctx["clock"].advance(3.0)
        ctx["controller"].tick()
        assert ctx["pet"].state is BehaviorState.STRETCHING

    def should_expire_stretch_back_to_idle_after_its_duration(self):
        ctx = make_ctx(idle_s=2.0, stretch_p=0.5, rng=StubRng(values=[0.1]))
        ctx["clock"].advance(3.0)
        ctx["controller"].tick()                     # → STRETCHING
        ctx["clock"].advance(2.6)                    # ≥ 2.5s 收场时长（10 帧一圈 1.25s，向上整圈=2.5s）
        ctx["controller"].tick_motion()
        assert ctx["pet"].state is BehaviorState.IDLE

    def should_expire_hurt_back_to_idle_without_any_bubble(self, ctx):
        """被戳又没开口时不能永远停在疼的表情（旧版只能靠气泡关闭回调收场）。"""
        ctx["controller"].on_press()
        ctx["controller"].on_release()               # → HURT
        ctx["controller"].tick()                     # 建立收场时刻
        ctx["clock"].advance(2.0)
        ctx["controller"].tick_motion()
        assert ctx["pet"].state is BehaviorState.IDLE


class TestAutonomousShowcase:
    """FR v1.17 C27：自主轮询要能轮到全部一次性状态，不只走/跑/伸懒腰。"""

    def should_rotate_through_every_showcase_state(self):
        from WisePet.app.pet_controller import SHOWCASE_STATES, showcase_state

        seen = {showcase_state(i) for i in range(1, len(SHOWCASE_STATES) + 1)}

        assert seen == set(SHOWCASE_STATES)

    def should_use_showcase_state_on_second_autonomous_trigger(self):
        from WisePet.app.pet_controller import SHOWCASE_STATES

        ctx = make_ctx(idle_s=0.0)
        controller = ctx["controller"]
        cfg = ctx["cfg"]
        now = ctx["clock"].now()

        controller._maybe_autonomous_action(now, cfg, False)
        assert ctx["pet"].state in (BehaviorState.WALKING, BehaviorState.RUNNING)
        ctx["pet"].stop_walk()                    # 归一回 IDLE

        controller._maybe_autonomous_action(now, cfg, False)

        assert ctx["pet"].state is SHOWCASE_STATES[0]

    def should_expire_speaking_thinking_and_sad_back_to_idle(self):
        for state in (BehaviorState.SPEAKING, BehaviorState.THINKING,
                      BehaviorState.SAD):
            ctx = make_ctx(idle_s=1e9)
            ctx["controller"]._transition_to(state, autonomous=True)
            ctx["controller"].tick()              # 建立收场起算点
            ctx["clock"].advance(4.0)             # 盖过最长收场（THINKING 10 帧一圈 1.25s → 3.75s）
            ctx["controller"].tick_motion()

            assert ctx["pet"].state is BehaviorState.IDLE, state

    def should_let_daytime_nap_last_long_enough_to_be_seen(self):
        ctx = make_ctx(idle_s=1e9)
        ctx["controller"]._transition_to(BehaviorState.SLEEPING, autonomous=True)
        ctx["controller"].tick()
        ctx["clock"].advance(1.0)
        ctx["controller"].tick()

        assert ctx["pet"].state is BehaviorState.SLEEPING


class TestShowcaseCoverage:
    """真机反馈 THINKING / SLEEPING 看不到：轮播漏号 + 顺序靠后 + 展示太短。"""

    def should_put_thinking_and_sleeping_first_in_rotation(self):
        from WisePet.app.pet_controller import SHOWCASE_STATES

        assert SHOWCASE_STATES[:2] == (BehaviorState.THINKING,
                                       BehaviorState.SLEEPING)

    def should_not_skip_showcase_index_when_sad_takes_an_even_trigger(self):
        """偶数次触发被 SAD 分支吃掉时，轮播序号不能被跳过。"""
        ctx = make_ctx(idle_s=0.0, vitals=VitalsVO(hunger=50, mood=10, energy=50))
        controller = ctx["controller"]
        cfg = ctx["cfg"]
        now = ctx["clock"].now()

        controller._maybe_autonomous_action(now, cfg, False)   # 1: 走路
        ctx["pet"].stop_walk()
        ctx["pet"].transition(BehaviorState.SAD)
        controller._maybe_autonomous_action(now, cfg, False)   # 2: SAD 分支，不演示
        ctx["pet"].stop_walk()                                 # SAD 分支会起步走路
        controller._maybe_autonomous_action(now, cfg, False)   # 3: 走路
        ctx["pet"].stop_walk()
        controller._maybe_autonomous_action(now, cfg, False)   # 4: 应轮到第 1 个

        assert ctx["pet"].state is BehaviorState.THINKING

    def should_hold_thinking_and_daytime_nap_long_enough(self):
        from WisePet.app.pet_controller import (NAP_MIN_DURATION_S,
                                                ONE_SHOT_DURATION_S)

        assert ONE_SHOT_DURATION_S[BehaviorState.THINKING] >= 3.0
        assert NAP_MIN_DURATION_S >= 10.0


class TestMotionTick:
    def should_advance_position_without_vitals_or_behavior_events(self):
        """tick_motion 只推位移：不驱动衰减/自主行为，免得高频通道干扰它们。"""
        ctx = make_ctx(idle_s=2.0, position=PositionVO(500, FLOOR_Y))
        ctx["clock"].advance(3.0)
        ctx["controller"].tick()                     # → WALKING
        ctx["events"].clear()
        ctx["clock"].advance(1.0)
        ctx["controller"].tick_motion()
        assert ctx["pet"].state is BehaviorState.WALKING
        assert ctx["pet"].position.x == 500 - round(ctx["cfg"].pet.walk_speed_px_per_s)
        assert ctx["events"] == []

    def should_not_walk_while_still_falling(self):
        """还在空中就不迈步：先落回地面再走（v2.0 C10）。"""
        ctx = make_ctx(idle_s=2.0, position=PositionVO(500, 100))
        ctx["clock"].advance(3.0)
        ctx["controller"].tick()                     # → WALKING（dt=0 不下落）
        x_before = ctx["pet"].position.x
        ctx["clock"].advance(0.1)                    # 90px，仍未落地
        ctx["controller"].tick_motion()
        assert ctx["pet"].position.y == 190
        assert ctx["pet"].position.x == x_before
