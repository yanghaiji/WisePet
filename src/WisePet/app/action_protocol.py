"""动作协议：把「模型回复 / 用户原话」解析成可本地执行的意图（工具执行层）。

AI 模式：系统提示词要求模型把要执行的操作输出成 ```json 代码块（或裸 JSON 对象），
本模块负责从中安全提取并校验；解析失败一律返回 None，按普通聊天文本展示。
本地模式（未配 Key）：用正则识别「N 分钟后提醒我 X」这类高频说法兜底。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

#: 番茄钟默认/最大时长（分钟）
DEFAULT_FOCUS_MIN = 25.0
MAX_FOCUS_MIN = 180.0

logger = logging.getLogger(__name__)

#: 支持的动作名
ACTION_REMIND = "remind"
ACTION_CREATE_FILE = "create_file"
ACTION_MAKE_PPT = "make_ppt"
_KNOWN_ACTIONS = (ACTION_REMIND, ACTION_CREATE_FILE, ACTION_MAKE_PPT)

#: 一次性提醒的最大延迟（分钟）：防止模型把单位算错排到一年后
MAX_DELAY_MIN = 60 * 24 * 30

#: 追加在系统提示词后的动作协议（注意：这段不能经 .format()，含大量花括号）
ACTION_PROMPT_GUIDE = """

【可执行操作协议·最高优先级】判断用户这条消息是否需要你「执行操作」：
- 定时提醒（X分钟后/X点提醒我…）→ 输出 {"action":"remind",...}
- 创建/写入文件 → 输出 {"action":"create_file",...}
- 生成PPT → 输出 {"action":"make_ppt",...}
需要执行时，你这条回复**只能**是一个 ```json 代码块，禁止输出安慰、解释或表情等任何
其他文字（执行结果由程序反馈给用户）。不需要执行时，忽略本协议正常聊天。格式：
{"action":"remind","delay_min":5,"text":"开会"}    （N 分钟后提醒；或用 "at_hm":"14:30" 指定时刻）
{"action":"create_file","path":"D:/notes/a.md","content":"文件完整内容"}
{"action":"make_ppt","path":"D:/demo.pptx","title":"演示标题","slides":[{"title":"页标题","bullets":["要点1","要点2"]}]}
要求：路径用用户指定的；slides 不超过 20 页、每页要点不超过 6 条。
"""


@dataclass(frozen=True)
class RemindAction:
    text: str
    delay_min: float = 0.0
    at_hm: str = ""          # "14:30" 绝对时刻（当天已过则排到明天）；与 delay_min 二选一
    daily: bool = False      # 每天重复（at_hm 生效时才有意义）


@dataclass(frozen=True)
class CreateFileAction:
    path: str
    content: str


@dataclass(frozen=True)
class MakePptAction:
    path: str
    title: str
    slides: tuple[dict, ...]   # [{"title": 页标题, "bullets": [要点...]}]


Action = RemindAction | CreateFileAction | MakePptAction


# ---------- AI 回复 → 动作 ----------

def parse_action(reply: str) -> Action | None:
    """从模型回复中提取动作 JSON；没有/不合法时返回 None（当普通文本处理）。"""
    obj = _extract_json_object(reply or "")
    if obj is None:
        return None
    try:
        return _to_action(obj)
    except (TypeError, ValueError):
        logger.debug("action json rejected: %r", obj)
        return None


def _extract_json_object(text: str) -> dict | None:
    """优先取 ```json 围栏块，再全段扫描第一个能解码出 dict 的 '{'。"""
    for match in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S):
        try:
            obj = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("action"):
            return obj
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("action"):
            return obj
    return None


def _to_action(obj: dict) -> Action:
    action = str(obj.get("action") or "").strip().lower()
    if action not in _KNOWN_ACTIONS:
        raise ValueError(f"unknown action: {action}")
    if action == ACTION_REMIND:
        text = str(obj.get("text") or "").strip()
        if not text:
            raise ValueError("remind text required")
        delay_min = float(obj.get("delay_min") or 0)
        at_hm = str(obj.get("at_hm") or "").strip()
        if delay_min <= 0 and not at_hm:
            raise ValueError("remind needs delay_min or at_hm")
        if delay_min and not 0 < delay_min <= MAX_DELAY_MIN:
            raise ValueError(f"delay out of range: {delay_min}")
        if at_hm and not re.fullmatch(r"\d{1,2}:\d{2}", at_hm):
            raise ValueError(f"bad at_hm: {at_hm}")
        return RemindAction(text=text, delay_min=delay_min, at_hm=at_hm)
    if action == ACTION_CREATE_FILE:
        path = str(obj.get("path") or "").strip()
        if not path:
            raise ValueError("create_file path required")
        return CreateFileAction(path=path, content=str(obj.get("content") or ""))
    path = str(obj.get("path") or "").strip()
    title = str(obj.get("title") or "").strip() or "未命名演示"
    slides = obj.get("slides")
    if not path or not isinstance(slides, list) or not slides:
        raise ValueError("make_ppt needs path and slides")
    cleaned: list[dict] = []
    for raw in slides[:30]:
        if not isinstance(raw, dict):
            continue
        bullets = tuple(str(b).strip() for b in (raw.get("bullets") or [])
                        if str(b).strip())
        slide_title = str(raw.get("title") or "").strip()
        if not slide_title and not bullets:
            continue
        cleaned.append({"title": slide_title, "bullets": bullets})
    if not cleaned:
        raise ValueError("make_ppt slides empty after clean")
    return MakePptAction(path=path, title=title, slides=tuple(cleaned))


# ---------- 本地正则兜底（未配 AI Key 时） ----------

_DELAY_RE = re.compile(
    r"(?:(?P<cn>[一二两三四五六七八九十百]+|半)|(?P<num>\d+(?:\.\d+)?))\s*个?\s*"
    r"(?P<unit>小时|钟头|分钟|分|秒钟|秒"
    r"|[smh](?:in(?:ute)?s?|ec(?:ond)?s?|r?s?|ours?)?)"
    r"\s*[之]?后?")
_LEAD_WORDS_RE = re.compile(r"^(?:请|麻烦|帮我?|记得)?(?:提醒我|叫我|通知我)")

#: 小写中文数字 → 整数（覆盖 1~99 的日常说法，如 十五/二十/三十五）
_CN_DIGIT = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_value(token: str) -> float:
    """「十五」→15、「二十五」→25、「半」→0.5；解析不了返回 0（调用方拒绝）。"""
    if token == "半":
        return 0.5
    if "百" in token:
        return 0.0                          # 上百分钟交给 AI/用户重说
    if "十" in token:
        left, _, right = token.partition("十")
        tens = _CN_DIGIT.get(left, 1) if left else 1
        ones = _CN_DIGIT.get(right, 0) if right else 0
        return float(tens * 10 + ones)
    return float(_CN_DIGIT.get(token, 0))


def _unit_minutes(unit: str) -> float:
    """单位 → 分钟系数；小时/钟头/h=60，秒/s=1/60，其余=1。"""
    if unit in ("小时", "钟头") or unit.startswith("h"):
        return 60.0
    if unit in ("秒钟", "秒") or unit.startswith("s"):
        return 1.0 / 60.0
    return 1.0


def parse_reminder_command(text: str) -> RemindAction | None:
    """「5分钟后提醒我开会」「30s后提醒我」「半小时后叫我喝水」→ RemindAction。"""
    stripped = str(text or "").strip()
    if not any(word in stripped for word in ("提醒", "叫我", "通知")):
        return None
    match = _DELAY_RE.search(stripped)
    if match is None:
        return None
    cn, num, unit = match.group("cn"), match.group("num"), match.group("unit")
    value = _cn_value(cn) if cn is not None else float(num)
    delay_min = value * _unit_minutes(unit)
    if not 0 < delay_min <= MAX_DELAY_MIN:
        return None
    rest = stripped[match.end():].strip()
    rest = _LEAD_WORDS_RE.sub("", rest).strip(" ，。,!?！？…")
    if not rest:
        return None
    return RemindAction(text=rest, delay_min=delay_min)


_DAILY_RE = re.compile(r"每天\s*(?P<hm>\d{1,2}[:：]\d{1,2})\s*[之]?后?提醒")
_CANCEL_RE = re.compile(r"取消(所有|全部)?(的)?(定时)?提醒")


def parse_daily_reminder_command(text: str) -> RemindAction | None:
    """「每天14:30提醒我交日报」→ RemindAction(at_hm)；不匹配返回 None。"""
    stripped = str(text or "").strip()
    match = _DAILY_RE.search(stripped)
    if match is None:
        return None
    hour, minute = (int(part) for part in match.group("hm").replace("：", ":").split(":"))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    rest = stripped[match.end():].strip()
    rest = _LEAD_WORDS_RE.sub("", rest)
    rest = re.sub(r"^我(要|得|需要)?", "", rest).strip(" ，。,!?！？…")
    if not rest:
        return None
    return RemindAction(text=rest, at_hm=f"{hour:02d}:{minute:02d}", daily=True)


def is_cancel_reminders_command(text: str) -> bool:
    """「取消提醒 / 取消所有提醒」→ True（清除全部挂起与每日提醒）。"""
    return bool(_CANCEL_RE.fullmatch(str(text or "").strip()))


def parse_focus_command(text: str, default_min: float = DEFAULT_FOCUS_MIN) -> float | None:
    """「专注25分钟」「来个番茄钟」→ 专注分钟数；没说时长用 default_min；不匹配返回 None。"""
    stripped = str(text or "").strip()
    if "番茄钟" in stripped:
        match = _DELAY_RE.search(stripped)
        if match is not None and match.group("unit") in ("分钟", "分"):
            return min(MAX_FOCUS_MIN, max(1.0, float(match.group("num"))))
        return default_min
    if "专注" in stripped:
        match = re.search(r"(\d+(?:\.\d+)?)\s*个?\s*分钟", stripped)
        if match is not None:
            return min(MAX_FOCUS_MIN, max(1.0, float(match.group(1))))
        return default_min
    return None


# ---------- 展示辅助 ----------

def describe_action(action: Action) -> str:
    """人话描述：确认弹窗与完成播报共用，避免把原始 JSON 抛给用户。"""
    if isinstance(action, RemindAction):
        if action.daily:
            return f"每天 {action.at_hm} 提醒你「{action.text}」"
        when = action.at_hm or f"{_pretty_delay(action.delay_min)}后"
        return f"{when}提醒你「{action.text}」"
    if isinstance(action, CreateFileAction):
        return f"创建文件：{action.path}"
    slides = "、".join(str(s.get("title") or "未命名页") for s in action.slides[:5])
    more = "…" if len(action.slides) > 5 else ""
    return f"生成 PPT「{action.title}」（{len(action.slides)} 页：{slides}{more}）"


def _pretty_delay(delay_min: float) -> str:
    if delay_min >= 60 and delay_min % 60 == 0:
        return f"{int(delay_min // 60)}小时"
    if delay_min < 1:
        return f"{int(round(delay_min * 60))}秒"
    return f"{round(delay_min)}分钟"
