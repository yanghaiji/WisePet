"""陪伴关系的纯逻辑：主动引用（惦记）、久别重逢、情境短语。

只吃「本机可观测事实」——聊天记录时间戳、系统时钟、专注/勿扰状态，
不联网、不画像、不猜测情绪。所有函数无副作用，便于直接单测。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: 一句话里出现这些词，视为「还没兑现的惦记」
_PENDING_RE = re.compile(
    r"明天|后天|改天|回头|等下|待会|晚点|下次|下周|之后|还没|打算|准备|记得|"
    r"得(?:去|做|写|交)|要(?:去|做|写|交|搞)")
#: 命令/工具类的话不该被当成惦记（否则「5分钟后提醒我开会」会被惦记好几天）
_COMMAND_RE = re.compile(r"提醒我|叫我|帮我(?:写|建|做|生成)|专注|番茄钟|"
                         r"取消(?:所有|全部)?提醒|写(?:个|一份)?文件|做个?\s*PPT")
#: 后续出现完成信号 → 保守认为已兑现，不打扰
_DONE_RE = re.compile(r"完成了|搞定了|做完了|弄好了|交了|已经(?:做完|搞定|交)|搞定啦|写好啦")

_MIN_LEN = 6
_MAX_LEN = 80
_SNIPPET_LEN = 26
_SECONDS_PER_DAY = 86_400.0

#: 不参与「完成信号到底指哪件事」判定的虚词 2-gram
_VAGUE_TOPICS = frozenset({
    "明天", "后天", "改天", "回头", "等下", "待会", "晚点", "下次", "下周",
    "之后", "还没", "打算", "准备", "记得", "已经", "完成", "搞定", "弄好",
    "交了", "谢谢", "现在", "这个", "那个", "一下", "可以", "什么", "怎么",
    "我们", "今天", "昨天", "自己", "一直", "不要", "没有", "一下", "的事",
})
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fa5]{2,}")
_LATIN_RE = re.compile(r"[a-z0-9_]{2,}")


def _topic_words(text: str) -> set[str]:
    """提取一句中文里可辨识的话题碎片（2-gram），用于把「搞定了」对上具体那件事。"""
    lowered = str(text or "").lower()
    words = set(_LATIN_RE.findall(lowered))
    for run in _CJK_RUN_RE.findall(lowered):
        for index in range(len(run) - 1):
            gram = run[index:index + 2]
            if gram not in _VAGUE_TOPICS:
                words.add(gram)
    return words


@dataclass(frozen=True)
class RecallCandidate:
    """一条值得主动提起的旧话。"""

    text: str          # 原话（已截断，供展示）
    ts: float
    days_ago: int

    @property
    def snippet(self) -> str:
        clean = re.sub(r"\s+", " ", self.text).strip()
        return clean[:_SNIPPET_LEN] + ("…" if len(clean) > _SNIPPET_LEN else "")


def _field(record, name: str, default=""):
    """兼容 ChatRecord 值对象与 dict（测试替身常用 SimpleNamespace/dict）。"""
    if isinstance(record, dict):
        return record.get(name, default)
    return getattr(record, name, default)


def resolve_key(candidate: RecallCandidate) -> str:
    """惦记指纹：同一句话即使时间变了也认得出来，用于去重与消解。"""
    import hashlib
    return hashlib.sha1(candidate.snippet.encode("utf-8")).hexdigest()[:10]


def pick_recalls(records, *, now: float, lookback_days: int = 14,
                 min_age_days: int = 1, limit: int = 3,
                 exclude: frozenset[str] | set[str] = frozenset()) -> list[RecallCandidate]:
    """挑出一组「还没兑现的惦记」（最近优先，默认最多 3 条）。

    规则（宁可不推也不乱推）：
    1. 只看用户说的话，长度适中，含未兑现词、不含命令词；
    2. 距今 ≥ min_age_days（刚说完不惦记）、≤ lookback_days；
    3. 该条之后若出现过「完成」信号，视为已兑现；
    4. 指纹在 exclude（已消解 / 已提过）里的跳过；
    5. 按时间倒序取 limit 条。
    """
    if lookback_days <= 0 or limit <= 0:
        return []
    records = list(records or ())          # 生成器也只能消费一次，先固化
    done_signals: list[tuple[float, set[str]]] = []
    items: list[tuple[float, str, int]] = []
    for record in records:
        if str(_field(record, "role", "")) != "user":
            continue
        content = str(_field(record, "content", "") or "").strip()
        try:
            ts = float(_field(record, "ts", 0) or 0)
        except (TypeError, ValueError):
            continue
        if _DONE_RE.search(content) and ts:
            done_signals.append((ts, _topic_words(content)))
        if not (_MIN_LEN <= len(content) <= _MAX_LEN) or not ts:
            continue
        if _COMMAND_RE.search(content) or not _PENDING_RE.search(content):
            continue
        age_days = (now - ts) / _SECONDS_PER_DAY
        if age_days < min_age_days or age_days > lookback_days:
            continue
        items.append((ts, content, int(age_days)))
    items.sort(key=lambda item: item[0], reverse=True)
    result: list[RecallCandidate] = []
    for index, (ts, content, age) in enumerate(items):
        # 完成信号只消解「对得上的那件事」；含糊的「搞定了」最多消解最新一条，
        # 不再一竿子打翻所有惦记——清单化要求多件事可以并存。
        topics = _topic_words(content)
        resolved = False
        for done_ts, done_topics in done_signals:
            if done_ts <= ts:
                continue
            if not done_topics or not topics:
                if index == 0:
                    resolved = True
            elif done_topics & topics:
                resolved = True
            if resolved:
                break
        if resolved:
            continue
        candidate = RecallCandidate(text=content, ts=ts, days_ago=age)
        if resolve_key(candidate) in exclude:
            continue
        result.append(candidate)
        if len(result) >= limit:
            break
    return result


def pick_recall(records, *, now: float, lookback_days: int = 14,
                min_age_days: int = 1) -> RecallCandidate | None:
    """兼容旧接口的单条版本（命令面板与既有测试仍在用）。"""
    found = pick_recalls(records, now=now, lookback_days=lookback_days,
                         min_age_days=min_age_days, limit=1)
    return found[0] if found else None


def recall_line(candidate: RecallCandidate, pet_name: str = "小墨") -> str:
    """本地兜底的惦记话术（未配 AI 时也能自然开口）。"""
    when = "前几天" if candidate.days_ago >= 3 else "昨天"
    return (f"{when}你说「{candidate.snippet}」，"
            f"{pet_name}一直记着呢～后来怎么样啦？")


def is_done_signal(text: str) -> bool:
    """用户明说「搞定了」→ 惦记与 followup 托付可以消解。"""
    return bool(_DONE_RE.search(str(text or "")))


def recall_prompt_line(candidate: RecallCandidate) -> str:
    """注入系统提示词：让模型知道有这么回事，但别硬聊。"""
    return recall_prompt_lines([candidate])


def recall_prompt_lines(candidates: list[RecallCandidate]) -> str:
    """多条惦记一起注入；明确要求不要一口气全倒出来。"""
    if not candidates:
        return ""
    lines = ["\n你惦记着这几件用户说过、之后没提过结果的事："]
    for candidate in candidates:
        lines.append(f"- {candidate.days_ago} 天前：「{candidate.snippet}」")
    lines.append("只有在话题自然相关时才轻轻提一句，绝不要一口气全列出来，"
                 "也不要每次都提。")
    return "\n".join(lines)


def reunion_greeting(gap_hours: float, pet_name: str = "小墨",
                     min_hours: float = 20.0) -> str | None:
    """按离开时长给一句本地「好久不见」；不够久返回 None。"""
    if gap_hours < min_hours:
        return None
    if gap_hours >= 72.0:
        return f"好几天没见啦，{pet_name}一直守在这儿等你呢～今天想聊点什么？"
    if gap_hours >= 36.0:
        return f"你这两天没来，{pet_name}有点想你啦～今天也一起加油吧！"
    return f"好久不见～{pet_name}刚醒，今天也陪着你！"


def reunion_hint(gap_hours: float, min_hours: float = 20.0) -> str:
    """给开机问候的 AI prompt 用的事实描述（不带话术，避免模型自己编时长）。"""
    if gap_hours < min_hours:
        return ""
    days = gap_hours / 24.0
    if days >= 2:
        return f"用户已经约 {int(days)} 天没打开过电脑上的你，语气可以带一点想念，但要克制。"
    return "用户隔了一天才回来，开场可以轻轻提一句好久不见。"


def context_phrase(*, hour: int, weekday: int, focusing: bool = False,
                   quiet: bool = False, just_finished_focus: bool = False) -> str:
    """把「现在是何时/用户处在什么处境」翻成一句短语，供话术与行为共用。"""
    if focusing or quiet:
        return "你正忙着，说话轻一点"
    if just_finished_focus:
        return "刚结束一场专注"
    if hour >= 23 or hour < 6:
        return "深夜"
    if weekday == 0 and 6 <= hour <= 9:
        return "周一早晨"
    if 6 <= hour <= 9:
        return "清晨"
    if 12 <= hour <= 14:
        return "午间"
    if 18 <= hour <= 22:
        return "晚上"
    return ""
