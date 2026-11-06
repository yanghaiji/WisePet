"""行为状态 ↔ 素材动作目录名的单一真相源（PRD v2.0 C12）。

此前动作清单在三处各写一份（`infra/frame_loader.py`、`ui/frame_animator.py`、
`tools/gen_pet_assets.py`），加动作时漏改任一处就会出现"素材生成了但运行时读不到"
这类静默故障。本版收敛到本模块，三处一律引用这里。

动画节拍（帧数与逐动作帧率）也属于这份素材契约：`app` 层要按「整圈」安排一次性动作
的收场时长，不能反过来依赖 `ui` 层的表（依赖方向 ui → app → domain）。

domain 层：只依赖同层的 state_machine，不引三方。
"""
from __future__ import annotations

from math import ceil

from .state_machine import BehaviorState

#: 全部素材动作目录名（assets/pet/{skin}/{action}/*.png），顺序即生成与预览顺序
ACTION_NAMES: tuple[str, ...] = (
    "idle", "walk", "run", "stretch", "hurt", "think", "eat", "speak", "sleep", "sad",
)

#: 每动作帧数：素材落盘契约（生成器与导入脚本都按这个数出图，缺一帧就不是同一个动画）
FRAMES_PER_ACTION = 10

#: 素材画布边长与脚线 y（`FRAME_FOOT_Y` 以下那 40px 是故意的透明余量）。
#: 生成器按它画、导入脚本按它对齐、运行时按它把宠物「放回地面」，三处共用一份数值：
#: 窗口底边就是贴地模式下的「地面」，缩到小画布时若仍按几何居中，那 40px 余量会
#: 等比缩成脚下空白，猫看着飘在半空（PRD v2.0 C10）。
FRAME_SOURCE_SIZE = 256
FRAME_FOOT_Y = 216

#: 脚线在素材高度中的占比（`216 / 256`）：与分辨率无关，所以 512 的原图同样适用
FRAME_FOOT_RATIO = FRAME_FOOT_Y / FRAME_SOURCE_SIZE

#: v1.x 就存在的核心动作：皮肤"已安装"只校验这批（否则旧素材会被整皮判为未安装）
LEGACY_ACTION_NAMES: frozenset = frozenset({
    "idle", "walk", "hurt", "think", "eat", "speak", "sleep", "sad",
})

#: 行为状态 → 动作目录名（DRAGGING 复用待机帧，被拖动本身无需专属动画）
STATE_TO_ACTION: dict[BehaviorState, str] = {
    BehaviorState.IDLE: "idle",
    BehaviorState.WALKING: "walk",
    BehaviorState.RUNNING: "run",
    BehaviorState.STRETCHING: "stretch",
    BehaviorState.DRAGGING: "idle",
    BehaviorState.HURT: "hurt",
    BehaviorState.THINKING: "think",
    BehaviorState.EATING: "eat",
    BehaviorState.SPEAKING: "speak",
    BehaviorState.SLEEPING: "sleep",
    BehaviorState.SAD: "sad",
}

#: 缺帧时的相似动作借用表：v2.0 新增动作在素材未重生时不至于退化成占位圆脸
SIMILAR_FALLBACK: dict[str, str] = {
    "run": "walk",
    "stretch": "idle",
}

#: 未知动作的兜底帧率（技术方案 D3：12FPS 兼顾观感与 CPU<3%）
FPS_FALLBACK = 12

#: 逐动作帧率（PRD v2.0 C11）：帧数统一 10 帧后，观感差异全靠这里调。
#: 共用一个全局 12FPS 时，10 帧走路一圈 1.0 秒（步频尚可），
#: 而 10 帧睡眠要慢到 2.5 秒一圈才看得出“呼吸”。
#: 数值依据 = 实测的相邻帧剪影变化量（`assets/pet/cat`，帧间不透明像素变化占比）：
#: 变化越猛越要快播，否则大跳变看着像抽搐；近静图的循环则要放慢，否则与 idle 同频
#: 就分不出情绪。`run`/`hurt` 单帧跳变最猛 → 16；`walk` → 10；`idle` → 6；
#: `sleep` 只有细微起伏 → 4（每帧 250ms 是换图不是呼吸）；`sad` 比待机还静 → 4
#:（放慢才读得出“低落”）。帧数从 6 提到 10 后同样帧率下每圈更长，观感更连贯
FPS_BY_ACTION: dict[str, int] = {
    "idle": 6,
    "walk": 10,
    "run": 16,
    "stretch": 8,
    "hurt": 16,
    "think": 8,
    "eat": 10,
    "speak": 10,
    "sleep": 4,
    "sad": 4,
}


def action_for_state(state: BehaviorState) -> str:
    """状态到动作名映射；未知状态安全回落 idle（新增状态漏配也不崩）。"""
    return STATE_TO_ACTION.get(state, "idle")


def frame_interval_ms(action: str) -> int:
    """动作对应的帧间隔（毫秒）：未知动作走全局 12FPS 兜底。"""
    return max(1, round(1000 / FPS_BY_ACTION.get(action, FPS_FALLBACK)))


def cycle_seconds(action: str, frames: int = FRAMES_PER_ACTION) -> float:
    """该动作播完一整圈要几秒：一次性动作的收场时长以它为节拍。"""
    fps = FPS_BY_ACTION.get(action, FPS_FALLBACK)
    return frames / fps


def whole_cycle_duration_s(action: str, at_least_s: float,
                           frames: int = FRAMES_PER_ACTION) -> float:
    """把「至少播多久」向上补齐到整数个动画循环。

    收场时刻落在循环中间时，最后一帧是任意一帧的姿态（吃到第 2 帧就切回待机），
    真机表现为“表情被从中间剪断”；补齐到整圈后每圈都从第 0 帧播回第 0 帧，接缝唯一。
    """
    cycle = cycle_seconds(action, frames)
    return round(cycle * max(1, ceil(at_least_s / cycle)), 3)
