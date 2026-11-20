"""近期处境：从用户明确说出的近况推导贴心的主动，而非凭空猜情绪。

与 auto_facts（长期稳定事实）的区别：处境有**时效**（加班几天、情绪一阵、病一场），
过期自然消退。

两类来源，都只认用户**自己明确说出口**的话：
1. 客观近况（加班/备考/生病/搬家/出差/找工作）；
2. 明确说出的情绪状态（好累/烦死了/压力大）——用户主动吐槽才算，
   不从客观事件反推「他是不是压力大」，也不脑补没说的感受。

用途：
1. 注入系统提示词，让回复自然贴合近况（情绪类可自然共情一句，但不渲染）；
2. 让主动关怀"看情况"：处境新鲜时偶尔一句贴合的关心，其余时候更安静。

全部纯函数、零依赖、无网络，便于单测。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_SECONDS_PER_DAY = 86400.0

#: 处境类别 → (触发词, 默认持续天数)。宁可漏，不可乱：只认明确的自我陈述。
#: 情绪类排在客观类**之后**，避免「加班好累」被判成 tired 而盖过 overtime。
_SITUATIONS: tuple[tuple[str, tuple[str, ...], float], ...] = (
    ("overtime", ("加班", "通宵", "赶工", "赶项目", "连轴转", "deadline", "熬夜赶"), 7.0),
    ("exam", ("备考", "考试", "考研", "期末", "刷题", "考公", "模拟考"), 14.0),
    ("sick", ("感冒", "发烧", "生病", "咳嗽", "看医生", "吃药", "打点滴", "不舒服"), 10.0),
    ("move", ("搬家", "装修", "搬东西", "打扫卫生"), 7.0),
    ("travel", ("出差", "出门几天", "去外地", "旅行", "旅游"), 7.0),
    ("job", ("面试", "投简历", "找工作", "跳槽", "辞职", "入职"), 14.0),
    # 情绪类：用户主动吐槽、来得快去得快，时效更短
    ("tired", ("好累", "太累", "累死", "好疲惫", "累趴", "筋疲力尽", "扛不住", "身心俱疲"), 2.0),
    ("annoyed", ("烦死", "好烦", "太烦", "气死", "崩溃", "受不了", "糟心"), 1.0),
    ("stressed", ("压力大", "压力好大", "压力太大", "压力山大", "绷不住", "顶不住", "焦虑"), 2.0),
)

#: 问句/转述/玩笑不记为自身处境
_NEG_RE = re.compile(r"吗？|吗\?|你是不是|别人|朋友|他|她|假如|如果|比如|段子|笑话")

#: 允许 AI 输出的处境类别白名单（防模型编造新类别）
SITUATION_KINDS: frozenset[str] = frozenset(k for k, _kw, _d in _SITUATIONS)

#: 情绪类处境：提示词允许适度共情，但不能当成长期状态渲染
_EMOTION_KINDS: frozenset[str] = frozenset({"tired", "annoyed", "stressed"})


def make_situation(kind: str, now: float) -> Situation | None:
    """按类别构造一条处境（供 AI 识别结果用）；非法类别返回 None。"""
    for name, _keywords, days in _SITUATIONS:
        if name == kind:
            return Situation(kind=name, since=now,
                             expires_at=now + days * _SECONDS_PER_DAY)
    return None

#: 主动关心话术（宠物口吻，贴合且不追问）
_CARE: dict[str, tuple[str, str]] = {
    "overtime": ("看你最近老加班，别熬太狠，记得起身喝口水～", "还在加班呀？注意身体，我陪你收个尾。"),
    "exam": ("复习辛苦啦，累了就歇会儿，我在这儿给你加油～", "还在看书呢？劳逸结合，别太绷着。"),
    "sick": ("听说你不舒服，多喝热水早点睡，我乖乖的不吵你～", "身体好点没？好好休息，别硬撑。"),
    "move": ("搬家够累的，记得歇歇别搬太猛～", "收拾得咋样啦？别累着，慢点来。"),
    "travel": ("出门在外照顾好自己呀，我在这边守着～", "出差还顺利吗？忙完记得歇歇。"),
    "job": ("面试/找工作挺费心的，别给自己太大压力～", "最近找工作还顺利吗？我在呢。"),
    "tired": ("听你说好累，先歇会儿吧，我陪你静静待着～", "还累着呢？别硬撑，喝口水歇歇。"),
    "annoyed": ("听着就挺糟心的，别气坏自己，我陪你缓缓～", "还在烦着呢？消消气，我在呢。"),
    "stressed": ("压力这么大，先深呼吸一下，别把自己逼太紧～", "还绷着吗？适当松松，我陪你。"),
}

#: 跟进节奏：首次关怀后隔一阵问一句近况，之后放慢到大约每天一次。
#: 具体发布仍受 QuietBudget 约束，宁可漏不可烦。
FOLLOW_UP_GAP = 6 * 3600.0
REGULAR_GAP = 20 * 3600.0


@dataclass(frozen=True)
class Situation:
    """一条近期处境。"""
    kind: str
    since: float          # 首次说出的时间
    expires_at: float     # 过期时间
    last_cared_day: str = ""   # 上次关怀的自然日（兼容旧数据）
    last_cared_at: float = 0.0 # 上次关怀时间戳，0 表示还没关心过
    care_count: int = 0        # 已关怀次数（0=首次，>0=跟进）

    def active(self, now: float) -> bool:
        return now < self.expires_at


def should_care(situation: Situation | None, now: float) -> bool:
    """是否到了关怀时机：首次立即，随后按间隔放慢；过期不关心。"""
    if situation is None or not situation.active(now):
        return False
    if situation.care_count <= 0:
        return True
    gap = FOLLOW_UP_GAP if situation.care_count == 1 else REGULAR_GAP
    return (now - situation.last_cared_at) >= gap


def detect_situation(text: str, now: float) -> Situation | None:
    """只从用户明确说出口的近况里识别一条处境（客观事件或情绪吐槽）。"""
    content = str(text or "").strip()
    if not content or _NEG_RE.search(content):
        return None
    for kind, keywords, days in _SITUATIONS:
        if any(kw in content for kw in keywords):
            return Situation(kind=kind, since=now,
                             expires_at=now + days * _SECONDS_PER_DAY)
    return None


def situation_prompt_line(situation: Situation | None, now: float) -> str:
    """注入系统提示词：让回复贴合近况，绝不把它们说成宠物自己的情绪。"""
    if situation is None or not situation.active(now):
        return ""
    label = {
        "overtime": "最近在加班赶工", "exam": "最近在备考",
        "sick": "最近身体不舒服", "move": "最近在搬家收拾",
        "travel": "最近出差/在外", "job": "最近在面试找工作",
        "tired": "刚说了好累", "annoyed": "刚说了很烦/糟心",
        "stressed": "刚说压力很大",
    }.get(situation.kind, "")
    if not label:
        return ""
    if situation.kind in _EMOTION_KINDS:
        return (f"\n用户{label}（他自己说出口的真实感受，可以自然共情一句，"
                "但别反复追问、别渲染、别当成长期状态）。回复时体贴一点。")
    return (f"\n用户{label}（他自己说的，别大肆渲染）。回复时自然体贴一点，"
            "少推娱乐玩闹话题，不要反复追问这件事，也不要替他表达情绪。")


def situation_care_line(situation: Situation | None, now: float) -> str:
    """首次贴合当下说一句，跟进时问一句近况；过期或未知类别返回空串。"""
    if situation is None or not situation.active(now):
        return ""
    lines = _CARE.get(situation.kind)
    if not lines:
        return ""
    elapsed_days = (now - situation.since) / _SECONDS_PER_DAY
    follow_up = situation.care_count > 0 or elapsed_days >= 3
    return lines[1] if follow_up else lines[0]
