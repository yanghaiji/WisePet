"""领域事件：进程内 EventBus 传输的不可变事实（技术方案 §3.5）。"""
from __future__ import annotations

from dataclasses import dataclass

from .state_machine import BehaviorState

#: 提醒/求助类型常量
KIND_WATER = "water"
KIND_SEDENTARY = "sedentary"
REASON_HUNGER = "hunger"
REASON_MOOD = "mood"
REASON_ENERGY = "energy"


@dataclass(frozen=True)
class VitalsChangedEvent:
    pet_id: str
    hunger: float
    mood: float
    energy: float
    changed: tuple = ()
    threshold_crossed: bool = False


@dataclass(frozen=True)
class BehaviorChangedEvent:
    pet_id: str
    old: BehaviorState
    new: BehaviorState


@dataclass(frozen=True)
class ChatReplyChunkEvent:
    text_delta: str
    done: bool = False
    reply: str = ""  # done=True 时的完整回复


@dataclass(frozen=True)
class ChatErrorEvent:
    kind: str  # AIGatewayError.KINDS 之一
    detail: str = ""


#: 会话阶段：一条用户消息从入队到回复结束的完整生命周期
PHASE_THINKING = "thinking"    # 已入队，等待/正在调用大脑
PHASE_REPLYING = "replying"    # 首段内容已到，流式上屏中
PHASE_DONE = "done"            # 本轮回复完成
PHASE_ERROR = "error"          # 本轮失败（兜底话术已接管）
PHASE_IDLE = "idle"            # 空闲，可发下一条


@dataclass(frozen=True)
class ChatStateChangedEvent:
    """会话状态事实：聊天窗据此显示「思考中/回复中/出错/空闲」与大脑来源。"""

    phase: str          # PHASE_* 之一
    source: str = ""    # ai / local，与日志 source= 同口径
    detail: str = ""    # PHASE_ERROR 时是错误类别
    session_id: str = ""


@dataclass(frozen=True)
class ChatTurnRecordedEvent:
    """一条完整对话记录（不是流式增量）：聊天窗回看与按天落盘共用这一条事实。"""

    role: str           # user / assistant / system
    content: str
    source: str = ""    # user / ai / local / fallback
    ts: float = 0.0
    session_id: str = ""


@dataclass(frozen=True)
class ReminderDueEvent:
    kind: str  # KIND_WATER / KIND_SEDENTARY
    text: str


@dataclass(frozen=True)
class HelpRequestEvent:
    pet_id: str
    reason: str  # REASON_* 之一


@dataclass(frozen=True)
class SoftJumpRequestedEvent:
    """自由漫游下的「柔和瞬移」请求：控制器选定目标点，ui 层淡出→落位→淡入。"""

    pet_id: str
    x: int
    y: int


@dataclass(frozen=True)
class HotkeyChatRequestedEvent:
    """全局快捷键（Ctrl+Alt+P）触发：从任意应用唤起聊天输入条。"""


@dataclass(frozen=True)
class SpeakRequestedEvent:
    """请求气泡播报（提醒/求助/AI 回复共用的通用通道）。"""

    text: str
    source: str = "system"  # system / reminder / help / ai / fallback
    title: str = ""         # 通知卡标题（空则按来源取默认）

    def __post_init__(self):
        if not str(self.text or "").strip():
            raise ValueError("speak text must not be blank")
