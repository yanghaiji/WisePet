"""PetController：驱动状态机、自主行为、拖拽/互动命令与事件分发（技术方案 §5）。"""
from __future__ import annotations

import logging
import random
import time
from typing import Callable

from ..domain.actions import STATE_TO_ACTION, whole_cycle_duration_s
from ..domain.config_model import AppConfig
from ..domain.product import (CONTEXT_REST, CONTEXT_SLEEP, CONTEXT_STRETCH,
                              BehaviorContext, context_hint)
from ..domain.errors import IllegalTransitionError
from ..domain.events import (
    REASON_ENERGY,
    REASON_HUNGER,
    REASON_MOOD,
    BehaviorChangedEvent,
    HelpRequestEvent,
    SoftJumpRequestedEvent,
    SpeakRequestedEvent,
    VitalsChangedEvent,
)
from ..domain.pet import Pet
from ..domain.position import Bounds, Facing, PositionVO, gravity_step
from ..domain.state_machine import BehaviorState
from ..domain.timeutil import is_in_night

logger = logging.getLogger(__name__)

#: 低状态主动求助话术（PRD US-4："我有点饿了…"）
HELP_TEXTS = {
    REASON_HUNGER: "我有点饿了…喂喂我好不好",
    REASON_MOOD: "心情有点低落…摸摸我的头吧",
    REASON_ENERGY: "好困呀…让我睡一会儿",
}

#: 抚摸时随机冒一句开心的回应（让「摸头」有可见反馈，而非只改心情值）
PET_HEAD_LINES = (
    "最喜欢你摸我头了~",
    "嘿嘿，再摸摸嘛",
    "好舒服呀…",
    "你今天也超温柔",
    "被你摸头就满足了",
)

#: 一次性动作的「至少播多久」（秒）：动画长度量级。ui 的动画播完回调仍先生效，谁先到算谁
# （PRD v2.0 C11）。以前 HURT/EATING 只能靠气泡关闭回调收场，被戳又没开口时会卡在疼的表情
ONE_SHOT_MIN_DURATION_S = {
    BehaviorState.HURT: 1.2,
    BehaviorState.EATING: 1.8,
    BehaviorState.STRETCHING: 1.5,
    BehaviorState.SPEAKING: 1.5,
    BehaviorState.THINKING: 3.0,
    BehaviorState.SAD: 1.5,
}

#: 真正的收场时长：把上面的下限向上补齐到整数个动画循环。
#: 直接按下限收场会把表情从中间剪断（实测 EATING 2.0s 对 0.6s 一圈 = 3.33 圈，
#: 第 2 帧的张嘴姿态直接跳回待机；STRETCHING 1.2 圈同理），整圈后接缝永远是同一个帧
ONE_SHOT_DURATION_S = {
    state: whole_cycle_duration_s(STATE_TO_ACTION[state], min_s)
    for state, min_s in ONE_SHOT_MIN_DURATION_S.items()
}

#: 自主行为轮播：每 SHOWCASE_EVERY 次自主触发，按固定顺序演示一个一次性状态。
#: 不额外消耗随机数，既保证「所有状态都会出现」，也不打乱既有概率分支的 rng 顺序。
SHOWCASE_EVERY = 2
#: THINKING / SLEEPING 排在最前：真机日志里这两个一次都没轮到（顺序靠后 + 演示太短 +
#: 序号被 SAD 分支吞掉），用户第 3 次反馈的就是它们看不见。
SHOWCASE_STATES: tuple[BehaviorState, ...] = (
    BehaviorState.THINKING, BehaviorState.SLEEPING, BehaviorState.STRETCHING,
    BehaviorState.EATING, BehaviorState.HURT, BehaviorState.SPEAKING,
    BehaviorState.SAD,
)
#: 白天打盹最短时长：别让 _maybe_wake_up 下一拍就把刚睡着的猫叫醒
NAP_MIN_DURATION_S = 10.0


def showcase_state(index: int) -> BehaviorState:
    """第 index 次轮播演示哪个状态（1-based，越界循环）。"""
    return SHOWCASE_STATES[(index - 1) % len(SHOWCASE_STATES)]

#: 对话态：这期间宠物原地不动（不落重力、不迈步、不收一次性动作），
#: 免得聊天时它还边走边下落，或者提醒把它拽向鼠标（FR v1.8 C14）
CONVERSATION_STATES = frozenset({BehaviorState.THINKING, BehaviorState.SPEAKING})

#: 落地微弹高度（像素）：纯位移，不为它多画一帧（PRD v2.0 C10）
LAND_BOUNCE_PX = 6

#: 到边折返后这一趟还想继续走的概率反面：随机数命中就收场（免得永远不停）
TRIP_END_CHANCE = 0.35

#: 低落状态的再次触发间隔：心情长期贴底时别让 SAD 占满全部时间，
#: 否则 IDLE 一出现就被拉走，跑 / 伸懒腰 / 睡觉永远没机会露面
SAD_REENTRY_S = 20.0

#: 未注入真实工作区时 bounds 的哨兵量级（组装根用 ±1e9），超过即认为"没有地面可依"
NO_FLOOR_BOUND = 10 ** 6


class PetController:
    """app 层门面。ui 只依赖本类的命令方法；时序由外部 tick() 驱动。"""

    def __init__(self, pet: Pet, bus, config_provider: Callable[[], AppConfig],
                 clock, rng: random.Random | None = None,
                 bounds_provider: Callable[[], Bounds] | None = None,
                 context_provider: Callable[[], BehaviorContext | None] | None = None):
        self._pet = pet
        self._bus = bus
        self._config = config_provider
        self._clock = clock
        self._context = context_provider or (lambda: None)
        self._rng = rng or random.Random()
        # 默认 bounds 足够大，由 ui 层注入真实工作区
        self._bounds_provider = bounds_provider or (lambda: (-10 ** 9, -10 ** 9, 10 ** 9, 10 ** 9))
        self._press_moved = False
        self._last_tick: float | None = None
        self._walk_direction = 1
        self._next_action_at = clock.now() + self._random_idle_gap()
        self._last_help_at: float | None = None
        self._last_sad_at: float | None = None
        self._autonomous_count = 0
        self._showcase_index = 0
        self._nap_at_night = False
        # 以下为 v2.0 位移与一次性动作的编排状态（PRD C10/C11）
        self._tracked_state: BehaviorState | None = None
        self._state_since: float | None = None
        self._walk_started_at: float | None = None
        self._wake_stretch_pending = False
        self._fall_active = False
        self._bounce_left = 0
        # 聊天窗打开期间整只宠物原地待着，不自主溜达也不被提醒拽走
        self._conversation_active = False
        # 柔和瞬移进行中（ui 淡出→落位→淡入）：期间冻结位移，防止动画被打架
        self._soft_jump_active = False
        self._last_jump_at: float | None = None

    @property
    def pet(self) -> Pet:
        return self._pet

    @property
    def conversation_active(self) -> bool:
        return self._conversation_active

    def set_conversation_active(self, active: bool) -> None:
        """聊天窗开关：打开时立刻收住走动/一次性动作，关闭后恢复自主行为。"""
        self._conversation_active = bool(active)
        if not active:
            return
        old = self._pet.state
        if old not in CONVERSATION_STATES:
            self._pet.stop_walk()
            if self._pet.state is not BehaviorState.IDLE:
                self._pet.finish_action()
        if self._pet.state is not old:
            self._publish_state_change(old)

    @property
    def _frozen(self) -> bool:
        """对话期间冻结位移：聊天窗开着，正在思考/说话，或柔和瞬移动画进行中。"""
        return (self._conversation_active or self._soft_jump_active
                or self._pet.state in CONVERSATION_STATES)

    def _roam(self, cfg: AppConfig | None = None) -> bool:
        """自由漫游开关：开启时不贴地、可停留在任意高度，并允许柔和瞬移。"""
        cfg = cfg or self._config()
        return bool(getattr(cfg.pet, "roam", False))

    # ---------- 周期驱动 ----------

    def tick(self, now: float | None = None) -> None:
        now = self._clock.now() if now is None else now
        cfg = self._config()
        is_night = is_in_night(time.localtime(now).tm_hour,
                               cfg.vitals.night_start_hour, cfg.vitals.night_end_hour)

        self._settle_vitals(now, cfg)
        if self._pet.can_autonomous() and not self._conversation_active:
            self._maybe_sad(cfg)
            self._maybe_autonomous_action(now, cfg, is_night)
            self._maybe_wake_up(is_night)
            self._maybe_request_help(now, cfg, is_night)
        self._maybe_soft_jump(now, cfg)
        self._advance_motion(now, cfg)
        self._last_tick = now

    def tick_motion(self, now: float | None = None) -> None:
        """只推进位移/重力/一次性动作收场，供 ui 的高频运动计时器调用（PRD v2.0 C10）。

        主计时器 1s 一次够衰减与提醒用，但 60px/s 的走动在 1s 粒度下是每秒瞬移一次，
        重力同理；所以位移单独用 50ms 驱动，且只在宠物真的在动时才跑（is_moving）。
        """
        now = self._clock.now() if now is None else now
        self._advance_motion(now, self._config())
        self._last_tick = now

    def is_moving(self) -> bool:
        """运动计时器开关依据：在走动/小跑，或贴地模式下还在下落途中。

        对话态一律不算在动：否则半空开聊时 50ms 计时器会一直空转。
        """
        state = self._pet.state
        if self._frozen or state is BehaviorState.DRAGGING:
            return False
        if state in (BehaviorState.WALKING, BehaviorState.RUNNING):
            return True
        if self._roam():
            return False               # 漫游模式没有地面：悬在半空不算「在下落」
        floor_y = self._floor_y()
        return (floor_y is not None and getattr(self._config().pet, "stick_to_floor", False)
                and self._pet.position.y < floor_y)

    def _track_state(self, now: float) -> None:
        """记录当前状态的进入时刻：一次性动作靠它自动收场。"""
        state = self._pet.state
        if state is not self._tracked_state:
            self._tracked_state = state
            self._state_since = now
            if state is BehaviorState.SLEEPING:
                # 夜间睡的按「天亮即醒」，白天轮播打盹才需要睡满 NAP_MIN_DURATION_S
                self._nap_at_night = self._is_night_now(self._config())

    def _settle_vitals(self, now: float, cfg: AppConfig) -> None:
        before = self._pet.vitals
        self._pet.settle_decay(now, cfg.vitals.rules, self._is_night_now(cfg))
        after = self._pet.vitals
        if after != before:
            crossed = (before.mood >= cfg.vitals.sad_threshold > after.mood)
            # 睡着时精力在往上走，不能再报成 decay：下游拿这标记判"为什么变了"
            cause = "rested" if after.energy > before.energy else "decay"
            self._bus.publish(VitalsChangedEvent(
                self._pet.pet_id, after.hunger, after.mood, after.energy,
                changed=(cause,), threshold_crossed=crossed))

    def _maybe_sad(self, cfg: AppConfig) -> None:
        # 只在"站着休息"时拉脸：不能从 WALKING/RUNNING 往 SAD 拉，否则难过的猫
        # 刚起身走一步就被下一 tick 拽回去，陷在 SAD↔走一步 的死循环里。
        if (self._pet.state is BehaviorState.IDLE
                and self._pet.is_feeling_sad(cfg.vitals.sad_threshold)):
            now = self._clock.now()
            # 心情贴在 floor 时数值只降不升，若无冷却则每次回 IDLE 都被立刻拉回 SAD，
            # 真机日志会变成 IDLE→SAD→WALKING→IDLE→SAD 的死循环，别的动作全看不见。
            if (self._last_sad_at is not None
                    and now - self._last_sad_at < SAD_REENTRY_S):
                return
            self._last_sad_at = now
            self._transition_to(BehaviorState.SAD, autonomous=True)

    def _maybe_autonomous_action(self, now: float, cfg: AppConfig, is_night: bool) -> None:
        state = self._pet.state
        if state not in (BehaviorState.IDLE, BehaviorState.SAD) or now < self._next_action_at:
            return
        self._next_action_at = now + self._random_idle_gap(cfg)
        self._autonomous_count += 1
        # 难过不等于瘫痪：低心情时仍偶尔起身慢慢踱两步（只走不跑、不懒腰），
        # 否则用户不主动投喂，它就永远卡在 SAD 定在屏幕一角不动。
        if state is BehaviorState.SAD:
            # 但睡觉不能被低落锁死：睡着才回精力，否则 energy 永远卡在 floor，
            # 而「低心情 → 只慢慢走 → 永远不睡」正好构成无法自解的死锁。
            # SAD→SLEEPING 不在白名单里，先归一到 IDLE 再睡（不改 domain 契约）。
            if is_night and self._pet.vitals.energy < cfg.vitals.sad_threshold:
                self._transition_to(BehaviorState.IDLE)
                self._transition_to(BehaviorState.SLEEPING, autonomous=True)
                return
            self._begin_stroll(now, running=False)
            return
        if self._wake_stretch_pending:
            # 睡醒下一 tick 先伸懒腰（本 tick 已先把求助话说出口）
            self._wake_stretch_pending = False
            self._transition_to(BehaviorState.STRETCHING, autonomous=True)
            return
        # 每 SHOWCASE_EVERY 次自主触发，按固定顺序演示一个一次性状态。
        # 排在概率分支之前且不调 rng：既有 stretch/run 概率测试的 StubRng 顺序不受影响。
        if self._autonomous_count % SHOWCASE_EVERY == 0:
            # 序号只在真正演示时递增：SAD / 睡醒分支会在偶数次触发时 return，
            # 若用 count//EVERY 就会漏号，靠后的状态永远轮不到（真机 SLEEPING 0 次）。
            self._showcase_index += 1
            self._transition_to(showcase_state(self._showcase_index),
                                autonomous=True)
            return
        # 因果驱动只作用于「非轮播」的那一次触发：轮播序号与覆盖度不受影响。
        # 不调 self._rng，既有 StubRng 顺序断言在没注入 context 时行为完全一致。
        hint = context_hint(self._context(), is_night)
        if hint is CONTEXT_REST:
            return                       # 专注 / 勿扰：原地安静，不位移不演示
        if hint is CONTEXT_STRETCH:
            self._transition_to(BehaviorState.STRETCHING, autonomous=True)
            return
        if hint is CONTEXT_SLEEP and is_night:
            self._transition_to(BehaviorState.SLEEPING, autonomous=True)
            return
        if is_night and self._pet.vitals.energy < cfg.vitals.sad_threshold:
            self._transition_to(BehaviorState.SLEEPING, autonomous=True)
        elif self._rng.random() < getattr(cfg.pet, "stretch_trigger_p", 0.2):
            self._transition_to(BehaviorState.STRETCHING, autonomous=True)
        else:
            # 这一趟是小跑还是走由概率定：偶尔提速才有"活物"感（PRD v2.0 C11）
            running = self._rng.random() < getattr(cfg.pet, "run_trigger_p", 0.25)
            self._begin_stroll(now, running=running)

    def _maybe_soft_jump(self, now: float, cfg: AppConfig) -> None:
        """自由漫游：每隔 roam_jump_interval_s 柔和瞬移到桌面随机位置（0 = 关闭）。

        ui 层负责淡出/淡入，出现不生硬；贴地模式（roam 关闭）永远不瞬移。
        首个 tick 只记时间不触发，避免启动瞬间就闪现。
        """
        if not self._roam(cfg) or self._soft_jump_active or self._frozen:
            return
        if self._pet.state is not BehaviorState.IDLE:
            return                      # 走动/睡觉/被打断时不抢戏
        interval = float(getattr(cfg.pet, "roam_jump_interval_s", 30.0))
        if interval <= 0:
            return
        if self._last_jump_at is None:
            self._last_jump_at = now
            return
        if now - self._last_jump_at < interval:
            return
        x1, y1, x2, y2 = self._bounds_provider()
        if abs(x1) > NO_FLOOR_BOUND or x2 <= x1 or y2 <= y1:
            return                      # 没有真实工作区（单测/异常）就不乱跳
        self._last_jump_at = now
        self._soft_jump_active = True
        target = PositionVO(self._rng.randint(x1, x2), self._rng.randint(y1, y2))
        logger.info("soft jump -> (%d, %d)", target.x, target.y)
        self._bus.publish(SoftJumpRequestedEvent(self._pet.pet_id, target.x, target.y))

    def finish_soft_jump(self) -> None:
        """ui 淡入完成的回调：解除瞬移冻结。

        不重排自主行为排期——重排会把走动/演示表情的节奏每 30s 往后推一遍，
        其它状态的出现频次就被瞬移挤没了（真机日志踩过）。
        """
        if not self._soft_jump_active:
            return
        self._soft_jump_active = False

    def _begin_stroll(self, now: float, *, running: bool) -> None:
        """起步去溜达：定方向与朝向，并把本趟起点刷成 now。"""
        target = BehaviorState.RUNNING if running else BehaviorState.WALKING
        self._transition_to(target, autonomous=True)
        if self._pet.state not in (BehaviorState.WALKING, BehaviorState.RUNNING):
            return                                    # 迁移被拒就不摆方向，免得朝向乱跳
        self._walk_direction = self._rng.choice((-1, 1))
        # 上一趟常正好停在边界上；此时若还朝外，第一步就「到边」被随机收趟，
        # 真机看到的是猫抖 0.05 秒又站住。只在正好贴边时强制朝屏幕内侧走开，
        # 离边还有几步的照常尊重随机方向（到边折返的既有玩法不变）。
        bounds = self._bounds_provider()
        x = self._pet.position.x
        if x <= bounds[0]:
            self._walk_direction = 1
        elif x >= bounds[2]:
            self._walk_direction = -1
        # 朝向跟着方向定：不然向左溜达时宠物是倒着走的
        self._pet.facing = Facing.RIGHT if self._walk_direction > 0 else Facing.LEFT
        self._walk_started_at = now

    def _floor_y(self) -> int | None:
        """贴地线 = 工作区下沿换算出的合法最大 y；未注入真实工作区时返回 None。"""
        floor_y = self._bounds_provider()[3]
        return None if abs(floor_y) > NO_FLOOR_BOUND else int(floor_y)

    def _advance_motion(self, now: float, cfg: AppConfig) -> None:
        """位移统一入口：先落回地面，再推进脚步，最后让一次性动作自己收场。

        状态时刻在本方法开头观测：这样本 tick 刚进入的一次性动作也能拿到正确的起算点，
        不会因"下一拍才被发现"而多撑一个 tick。
        """
        self._track_state(now)
        # 一次性动作仍需自动收场（聊天气泡关闭回调之前也有兜底），只冻结位移本身
        if not self._frozen:
            self._apply_gravity(now, cfg)
            self._advance_locomotion(now, cfg)
        self._expire_one_shot(now)

    def _apply_gravity(self, now: float, cfg: AppConfig) -> None:
        """贴地重力：非拖拽、非对话且高于地面时逐 tick 下落（PRD v2.0 C10）。

        自由漫游（roam）优先于贴地：漫游的猫住在整张桌面，不再被拽回地面线。
        """
        if not getattr(cfg.pet, "stick_to_floor", False) or self._roam(cfg):
            return
        floor_y = self._floor_y()
        if floor_y is None or self._frozen or self._pet.state is BehaviorState.DRAGGING:
            return
        pos = self._pet.position
        if pos.y >= floor_y:
            self._fall_active = False
            return
        if not self._fall_active:
            self._fall_active = True
            self._bounce_left = 1 if LAND_BOUNCE_PX > 0 else 0
        dt = 0.0 if self._last_tick is None else max(0.0, now - self._last_tick)
        new_y, landed = gravity_step(pos.y, floor_y, dt, cfg.pet.fall_speed_px_per_s)
        if landed and self._bounce_left > 0:
            # 落地微弹：抬 6px 再落一次，只弹一下不做物理仿真
            self._bounce_left -= 1
            self._pet.position = PositionVO(pos.x, floor_y - LAND_BOUNCE_PX)
            return
        self._pet.position = PositionVO(pos.x, new_y)
        if landed:
            self._fall_active = False

    def _advance_locomotion(self, now: float, cfg: AppConfig) -> None:
        """走动/小跑：走满 walk_max_s 就收趟，到边折返时也可能被随机提前喊停。"""
        state = self._pet.state
        if state not in (BehaviorState.WALKING, BehaviorState.RUNNING) or self._last_tick is None:
            return
        # 自由摆放/漫游模式下没有「地面」概念：不能拿屏幕下沿拦住任意高度的走动
        floor_y = (self._floor_y()
                   if getattr(cfg.pet, "stick_to_floor", False) and not self._roam(cfg)
                   else None)
        # 阈值留出微弹高度：否则每落地一次就要多等一个 tick 才能继续走，脚步会顿一下
        if floor_y is not None and self._pet.position.y < floor_y - LAND_BOUNCE_PX:
            return  # 还在下落，先落回地面再走
        walk_max = getattr(cfg.pet, "walk_max_s", 12.0)
        elapsed = (0.0 if self._walk_started_at is None
                   else max(0.0, now - self._walk_started_at))
        # 每拍都判时长：旧代码只在「撞到边」时才检查，宽屏上走到边要十几秒，
        # walk_max_s 形同虚设，猫能连着走半分钟，其它状态基本没机会露面。
        # 降速要排在收趟前：跑累的那一拍先退回 WALKING 并重开一趟计时，
        # 否则 walk_max_s 一到就直接急停，v2.0 C11 的「减速而不是急停」就没了。
        if state is BehaviorState.RUNNING and elapsed >= walk_max * 0.6:
            self._transition_to(BehaviorState.WALKING)
            self._walk_started_at = now
            state = BehaviorState.WALKING
            elapsed = 0.0
        if elapsed >= walk_max:
            self._transition_to(BehaviorState.IDLE)
            self._reschedule_idle(now, cfg)
            return
        dt = max(0.0, now - self._last_tick)
        bounds = self._bounds_provider()
        speed = (getattr(cfg.pet, "run_speed_px_per_s", 150.0)
                 if state is BehaviorState.RUNNING
                 else getattr(cfg.pet, "walk_speed_px_per_s", 60.0))
        step = int(round(speed * dt)) * self._walk_direction
        new_pos = self._pet.position.shifted(step, 0)
        if new_pos.x <= bounds[0] or new_pos.x >= bounds[2]:
            self._walk_direction = -self._walk_direction
            self._pet.facing = self._pet.facing.flipped()
            self._pet.position = new_pos.clamped_to(bounds)
            if self._should_end_trip(now):
                self._transition_to(BehaviorState.IDLE)
                self._reschedule_idle(now, cfg)
        else:
            self._pet.drag_by(step, 0)  # 复用位置位移（非拖拽语义，仅坐标更新）

    def _should_end_trip(self, now: float) -> bool:
        """到边折返时是否提前收趟；时长上限已在 _advance_locomotion 每拍处理。"""
        if self._walk_started_at is None:
            self._walk_started_at = now
            return False
        return self._rng.random() < TRIP_END_CHANCE

    def _expire_one_shot(self, now: float) -> None:
        """一次性动作自动收场：轮播演示出来的状态也必须能自己回到 IDLE。"""
        state = self._pet.state
        duration = ONE_SHOT_DURATION_S.get(state)
        if duration is None or self._state_since is None:
            return
        if now - self._state_since < duration:
            return
        if state is BehaviorState.THINKING:
            # Pet.finish_action() 的集合里没有 THINKING（它本是等应答的会话态），
            # 但 THINKING→IDLE 在白名单里，直接迁移再统一发布一次状态变化。
            self._pet.transition(BehaviorState.IDLE)
        else:
            self._pet.finish_action()
        self._publish_state_change(state)
        self._reschedule_idle(now, self._config())

    def _maybe_wake_up(self, is_night: bool) -> None:
        """天亮叫醒；白天打盹也要睡满 NAP_MIN_DURATION_S，别下一拍就被叫起来。"""
        if (self._pet.state is not BehaviorState.SLEEPING or is_night
                or self._tracked_state is not BehaviorState.SLEEPING):
            return                      # _tracked_state 还没刷成 SLEEPING = 本拍刚入睡
        now = self._clock.now()
        if (not self._nap_at_night
                and self._state_since is not None
                and now - self._state_since < NAP_MIN_DURATION_S):
            return
        self._pet.finish_action()
        self._publish_state_change(BehaviorState.SLEEPING)
        # 睡醒记一笔"想起身伸个懒腰"，下一 tick 再伸：本 tick 先让求助把话说完
        self._wake_stretch_pending = True
        self._next_action_at = now

    def _maybe_request_help(self, now: float, cfg: AppConfig, is_night: bool) -> None:
        if self._pet.state not in (BehaviorState.IDLE, BehaviorState.SAD):
            return
        vitals = self._pet.vitals
        reason = None
        if vitals.hunger < cfg.vitals.danger_threshold:
            reason = REASON_HUNGER
        elif vitals.mood < cfg.vitals.danger_threshold:
            reason = REASON_MOOD
        elif vitals.energy < cfg.vitals.danger_threshold and not is_night:
            reason = REASON_ENERGY
        if reason is None:
            return
        if (self._last_help_at is not None
                and now - self._last_help_at < cfg.vitals.help_cooldown_s):
            return
        self._last_help_at = now
        self._bus.publish(HelpRequestEvent(self._pet.pet_id, reason))
        self._bus.publish(SpeakRequestedEvent(HELP_TEXTS[reason], source="help"))

    # ---------- 交互命令（ui 调用） ----------

    def on_press(self) -> None:
        """按下即进入拖拽候选；无位移松手视为点击（PRD US-1/US-2）。"""
        self._press_moved = False
        if self._soft_jump_active:
            self._soft_jump_active = False    # 用户中途抓住：瞬移让位给拖拽
        self._try_transition(lambda: self._pet.start_drag(), "DRAGGING")

    def on_drag(self, dx: int, dy: int) -> None:
        if self._pet.state is BehaviorState.DRAGGING:
            if dx or dy:
                self._press_moved = True
            self._pet.drag_by(dx, dy)

    def on_release(self) -> None:
        if self._pet.state is not BehaviorState.DRAGGING:
            return
        old = BehaviorState.DRAGGING
        self._pet.release_drag(self._bounds_provider())
        self._publish_state_change(old)
        if not self._press_moved:
            self.on_click()  # 原地松手 = 点击 → 被戳反应

    def on_click(self) -> None:
        """被戳一下：HURT 动画 + 心情小幅下降。"""
        old = self._pet.state
        self._pet.stop_walk()
        if self._pet.state is not old:
            self._publish_state_change(old)
        self._try_transition(lambda: self._pet.hurt(), "HURT")

    def feed(self) -> None:
        """投喂：先打断一切可中断状态回待机，再进入 EATING。

        domain 层 Pet.feed 只在 IDLE 合法（不变量 I1），所以这里先把走/跑/被戳/伸懒腰/
        睡/低落收回到 IDLE，再喂。拖拽中、AI 正在思考或对话时不喂（前者手在拖、后者在等
        回复）；已经在吃则不叠加。
        """
        state = self._pet.state
        if state in (BehaviorState.DRAGGING, BehaviorState.THINKING, BehaviorState.SPEAKING):
            return
        if state is BehaviorState.EATING:
            self._publish_vitals()
            return
        if state is not BehaviorState.IDLE:
            self._pet.stop_walk()              # 走/跑 → IDLE
            if self._pet.state is not BehaviorState.IDLE:
                self._pet.finish_action()      # 其余一次性动作 → IDLE
        if self._pet.state is BehaviorState.IDLE:
            self._try_transition(lambda: self._pet.feed(), "EATING")
        self._publish_vitals()

    def pet_head(self) -> None:
        """抚摸：提升心情；并随机冒一句开心回应，让操作有可见反馈。

        AI 正在思考/对话时不抢话（那两条状态不打断）；其余状态只浮一句气泡，
        不强制切到 SPEAKING——从走/跑/被戳等状态喂气泡时状态机自然会忽略非法迁移，
        宠物继续手头动作，只多一句开心的话。
        """
        self._pet.pet_head()
        self._publish_vitals()
        if self._pet.state not in (BehaviorState.THINKING, BehaviorState.SPEAKING):
            self.speak(self._rng.choice(PET_HEAD_LINES), source="pet")

    def speak(self, text: str, source: str = "system") -> None:
        self._bus.publish(SpeakRequestedEvent(text, source=source))

    def start_thinking(self) -> None:
        """开始一轮对话：先把走/跑/被戳/说话收回 IDLE 再进 THINKING。

        点击宠物会先经 on_release→on_click 落到 HURT，从 HURT/WALKING 直接进
        THINKING 是非法迁移（会被静默吞掉，宠物继续走），所以这里必须归一化。
        """
        if self._pet.state is BehaviorState.THINKING:
            return  # 上一轮还在等回复，不重复迁移
        self._settle_to_idle_for_conversation(exclude=())
        self._try_transition(lambda: self._pet.start_thinking(), "THINKING")

    def start_speaking(self) -> None:
        """气泡开口：THINKING→SPEAKING 合法；从其它态也要能打断后开口。"""
        if self._pet.state is BehaviorState.THINKING:
            self._try_transition(lambda: self._pet.start_speaking(), "SPEAKING")
            return
        self._settle_to_idle_for_conversation(exclude=(BehaviorState.SPEAKING,))
        self._try_transition(lambda: self._pet.start_speaking(), "SPEAKING")

    def _settle_to_idle_for_conversation(self, exclude=()) -> None:
        """对话前置：把可中断行为收回待机；拖拽中不打断（手在拖它）。

        exclude 里的状态即使出现在白名单外也不动（如已在 SPEAKING 就不必重开口）。
        """
        if self._pet.state in (BehaviorState.DRAGGING, BehaviorState.IDLE) or \
                self._pet.state in exclude:
            return
        old = self._pet.state
        self._pet.stop_walk()
        if self._pet.state is not BehaviorState.IDLE:
            self._pet.finish_action()
        if self._pet.state is not old:
            self._publish_state_change(old)

    def on_action_finished(self) -> None:
        """ui 动画播完/气泡关闭回调：一次性动作回待机。"""
        old = self._pet.state
        self._pet.finish_action()
        if old is not self._pet.state:
            self._publish_state_change(old)

    def move_to(self, x: int, y: int) -> None:
        """提醒时靠近鼠标等主动位移（PRD US-5），落位钳制。

        贴地模式下只取目标的 x：语义从"飞到鼠标旁"改成"走过去提醒你"（PRD v2.0 C10）；
        漫游模式保留完整目标点——猫可以住在桌面任何高度。柔和瞬移进行中要放行：
        落位正是瞬移动画链条的一环。
        """
        if self._pet.state is BehaviorState.DRAGGING or \
                (self._frozen and not self._soft_jump_active):
            return  # 对话中不许提醒把宠物拽走
        cfg = self._config()
        bounds = self._bounds_provider()
        old = self._pet.state
        target_y = y
        if getattr(cfg.pet, "stick_to_floor", False) and not self._roam(cfg):
            floor_y = self._floor_y()
            if floor_y is not None:
                target_y = floor_y
        self._pet.stop_walk()
        self._pet.position = PositionVO(x, target_y).clamped_to(bounds)
        if old is not self._pet.state:
            self._publish_state_change(old)

    # ---------- 内部工具 ----------

    def _random_idle_gap(self, cfg: AppConfig | None = None) -> float:
        cfg = cfg or self._config()
        lo = min(cfg.pet.idle_min_s, cfg.pet.idle_max_s)
        hi = max(cfg.pet.idle_min_s, cfg.pet.idle_max_s)
        # 活泼度只缩放「多久出发一次」，不改状态机与轮播覆盖；normal 保持原语义
        factor = {"quiet": 1.8, "lively": 0.6}.get(
            getattr(cfg.pet, "activity", "normal"), 1.0)
        return self._rng.uniform(lo, hi) * factor

    def _reschedule_idle(self, now: float, cfg: AppConfig | None = None) -> None:
        """收场后重新排空闲间隔。

        旧代码只在「触发自主行为」时排下一次，一趟溜达动辄二三十秒，排期早已过期；
        一回到 IDLE 下一拍就又出发，待机几乎不存在，其它状态也就没机会出现。
        """
        self._next_action_at = now + self._random_idle_gap(cfg)

    def _is_night_now(self, cfg: AppConfig) -> bool:
        return is_in_night(time.localtime(self._clock.now()).tm_hour,
                           cfg.vitals.night_start_hour, cfg.vitals.night_end_hour)

    _walk_direction: int

    def _try_transition(self, action: Callable[[], None], what: str = "") -> None:
        """迁移唯一入口：非法迁移记一条 debug 并静默忽略，绝不打断 tick 循环。"""
        old = self._pet.state
        try:
            action()
        except IllegalTransitionError:
            logger.debug("transition ignored: %s -> %s", old.name, what or "action")
            return
        if old is not self._pet.state:
            logger.info("behavior %s -> %s", old.name, self._pet.state.name)
            self._bus.publish(BehaviorChangedEvent(self._pet.pet_id, old, self._pet.state))

    def _transition_to(self, target: BehaviorState, autonomous: bool = False) -> None:
        """编排侧迁移：兜底只写一份（_try_transition），这里不再叠一层 try/except。"""
        self._try_transition(lambda: self._pet.transition(target, autonomous), target.name)

    def _publish_state_change(self, old: BehaviorState) -> None:
        if old is not self._pet.state:
            logger.info("behavior %s -> %s", old.name, self._pet.state.name)
            self._bus.publish(BehaviorChangedEvent(self._pet.pet_id, old, self._pet.state))

    def _publish_vitals(self) -> None:
        v = self._pet.vitals
        self._bus.publish(VitalsChangedEvent(self._pet.pet_id, v.hunger, v.mood, v.energy))
