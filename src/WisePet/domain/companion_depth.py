"""情感陪伴深度：记忆可见化 + 情绪语气 + 作息共鸣（纯函数，零依赖）。

对应「A 情感陪伴」路线的三件事：
- 第 1 件：把「它记得你」从命令面板搬到自然对话——生成 prompt 引导，
  让模型在相关话题主动、克制地引用已记住的事实，而不是被问才查。
- 第 3 件：让宠物「活」起来——按心情/精力给语气指引，按作息给共鸣话术。
- 第 5 件：可发现性——按用户此刻的处境，提示模型自然带出某项能力，
  而不是让用户去翻 20+ 命令。

所有函数无 IO、无 Qt、无网络，便于单测。真正的调度/注入在 app 与组装根。
"""
from __future__ import annotations


# ---------- 第 1 件：记忆可见化 ----------

def memory_visibility_line(facts_summary: str) -> str:
    """把长期事实转成「自然提起」的提示词引导（有事实才返回）。

    与 long_term_memory.prompt_line 的「被问才答」互补：这里鼓励在话题
    自然相关时主动引用一次，但严格克制——不许炫耀记忆、不许每次都提。
    """
    if not str(facts_summary or "").strip():
        return ""
    return ("\n【记得的事 · 自然提起】上面【长期记忆】里的事，是用户说过的。"
            "当话题自然相关时（比如他提到工作/兴趣/近况），可以很自然地接一句"
            "「你之前说过的XX，后来怎么样了」，让用户感觉到你记得；"
            "但只在确实相关时提一次，不要炫技、不要每句都提、不要一次列多条。")


def name_first_line(has_name: bool, first_message: bool = True) -> str:
    """首次对话且已知名字时，鼓励用名字称呼——制造「它记得我」的第一下惊喜。"""
    if not has_name or not first_message:
        return ""
    return "\n用户告诉过你他的名字，开场自然地叫一次他的名字，别像陌生人。"


# ---------- 第 3 件：情绪语气 + 作息共鸣 ----------

def mood_tone_line(mood: float, energy: float) -> str:
    """按 pet 的心情/精力给语气指引（数值来自 VitalsVO，0~100）。"""
    try:
        m, e = float(mood), float(energy)
    except (TypeError, ValueError):
        return ""
    if m < 25:
        return ("\n此刻心情低落：说话慢一点、软一点，少开玩笑，"
                "多一点安静的陪伴；不要强行热闹。")
    if e < 25:
        return ("\n此刻很疲惫：语气轻一点、短一点，像也困了的样子，"
                "可以轻轻提一句休息。")
    if m >= 80 and e >= 70:
        return ("\n此刻心情很好、精力充沛：说话可以活泼俏皮一点，带点小兴奋。")
    return ""


def schedule_empathy_line(hour: int, weekday: int, idle_hours: float = 0.0) -> str:
    """作息共鸣：深夜/周一/久别回来的开场语气，帮宠物像「一起生活」而非机器。"""
    if hour >= 23 or hour < 6:
        return "\n现在是深夜：语气轻、像小声说话，可温柔提醒早点休息。"
    if idle_hours >= 20:
        return "\n用户隔了挺久才回来：可以带一点想念，但要克制、不抱怨。"
    if weekday == 0 and 6 <= hour <= 9:
        return "\n周一早晨：语气精神一点，像在给用户打气。"
    return ""


def sulk_line(idle_hours: float, mood: float) -> str:
    """闹小脾气：久不理 + 心情偏低时，给一句带情绪的本地话术（可主动发）。"""
    try:
        idle, m = float(idle_hours), float(mood)
    except (TypeError, ValueError):
        return ""
    if idle >= 24 and m < 50:
        return "哼，好久都不理我…我又不会跑掉，你回来啦？"
    return ""


# ---------- 第 5 件：可发现性（能力自然浮现） ----------

_CAPABILITY_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("累", "疲惫", "困", "加班", "熬夜"),
     "用户看起来累了：可以自然问一句要不要设个休息提醒，或帮他排个番茄钟。"),
    (("忘了", "记不住", "提醒我", "别忘了", "记着"),
     "用户提到怕忘事：可以自然提一句「要我帮你记着/到点提醒你吗」。"),
    (("查", "搜", "新闻", "天气", "最新", "今天"),
     "用户可能想要实时信息：记得你联网搜索/天气工具，必要时主动提出帮他查。"),
    (("附近", "周边", "哪有", "美食", "咖啡"),
     "用户问本地生活：可以用位置与周边工具查真实结果。"),
)


def discoverability_line(user_text: str) -> str:
    """按用户此刻的话，提示模型自然带出某项能力（每次最多一条，避免像广告）。"""
    text = str(user_text or "")
    if not text.strip():
        return ""
    for keywords, hint in _CAPABILITY_HINTS:
        if any(k in text for k in keywords):
            return "\n【顺势帮忙】" + hint + " 只在确实贴合时提，别生硬推销。"
    return ""
