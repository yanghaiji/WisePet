"""托付任务的纯逻辑：自然语言 → 任务规格、重试退避、只读沙箱判定。

「托付」与「一次性动作」的区别：任务会跨 tick 存活、失败会重试、用户能取消/消解。
这里只定义数据结构与纯函数，不碰时钟、文件系统与事件总线。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

#: 任务种类
WATCH_FILE = "watch_file"
FOLLOWUP = "followup"

#: 单次读取的文件上限（字节）：只读沙箱的硬边界
MAX_FILE_BYTES = 512 * 1024
#: 单次重试退避上限（秒）
MAX_BACKOFF_S = 300.0
#: 反复催人至少隔 10 分钟，避免变成骚扰
MIN_FOLLOWUP_S = 600.0

_WATCH_RE = re.compile(
    r"(?:盯着|看着|监视|帮我盯)\s*(?P<path>[^\s，,]+)\s*"   # 允许 Windows 盘符冒号
    r"[，,]\s*(?:出现|有|包含|发现)\s*(?P<keyword>[^\s，,。！!]{1,30})")
_FOLLOWUP_RE = re.compile(
    r"每隔\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>分钟|小时|min|h)\s*"
    r"(?:提醒|催)\s*(?:我)?(?P<goal>.{2,60})")
_DONE_RE = re.compile(r"搞定了|完成了|做完了|弄好了|交了|已经(?:做完|搞定|交)|不用(?:催|提醒)了")


@dataclass(frozen=True)
class TaskSpec:
    """一条托付（用户原话 + 可执行规格）。"""

    id: str
    kind: str
    text: str                 # 人话描述，用于气泡与列表
    target: str = ""          # watch_file 的文件路径
    match: str = ""           # watch_file 的关键词
    interval_s: float = 0.0   # followup 的间隔
    max_tries: int = 5
    created_at: float = 0.0
    next_at: float = 0.0
    tries: int = 0
    status: str = "pending"   # pending / done / failed / cancelled
    last_error: str = ""
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "text": self.text,
            "target": self.target, "match": self.match,
            "interval_s": self.interval_s, "max_tries": self.max_tries,
            "created_at": self.created_at, "next_at": self.next_at,
            "tries": self.tries, "status": self.status,
            "last_error": self.last_error, "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TaskSpec":
        return cls(
            id=str(data.get("id") or ""), kind=str(data.get("kind") or ""),
            text=str(data.get("text") or ""), target=str(data.get("target") or ""),
            match=str(data.get("match") or ""),
            interval_s=float(data.get("interval_s") or 0.0),
            max_tries=int(data.get("max_tries") or 5),
            created_at=float(data.get("created_at") or 0.0),
            next_at=float(data.get("next_at") or 0.0),
            tries=int(data.get("tries") or 0),
            status=str(data.get("status") or "pending"),
            last_error=str(data.get("last_error") or ""),
            meta=dict(data.get("meta") or {}))


def parse_task_command(text: str, now: float = 0.0) -> TaskSpec | None:
    """把「盯着 x.log，出现 ERROR 告诉我」「每隔30分钟催我交周报」翻成任务。

    解析不了就返回 None，交给 AI 当普通聊天——不猜、不硬凑。
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    watch = _WATCH_RE.search(raw)
    if watch is not None:
        path = watch.group("path").strip("'\"")
        keyword = watch.group("keyword").strip("，,。！!告诉我")
        if not path or not keyword:
            return None
        return TaskSpec(
            id=_make_id(raw, now), kind=WATCH_FILE,
            text=f"盯着 {path}，出现「{keyword}」就告诉你",
            target=path, match=keyword, interval_s=30.0,
            max_tries=0,           # 0 = 不限次数，直到命中或用户取消
            created_at=now, next_at=now + 30.0)
    follow = _FOLLOWUP_RE.search(raw)
    if follow is not None:
        value = float(follow.group("num"))
        unit = follow.group("unit")
        interval_s = value * (3600.0 if unit in ("小时", "h") else 60.0)
        goal = follow.group("goal").strip(" 。！!，,")
        if interval_s < MIN_FOLLOWUP_S or not goal:
            return None
        return TaskSpec(
            id=_make_id(raw, now), kind=FOLLOWUP,
            text=f"每隔 {value:g} {unit}提醒你：{goal}",
            interval_s=interval_s, max_tries=5,
            created_at=now, next_at=now + interval_s)
    return None


def _make_id(text: str, now: float) -> str:
    digest = hashlib.sha1(f"{text}|{now}".encode("utf-8")).hexdigest()[:10]
    return f"t-{digest}"


def is_done_signal(text: str) -> bool:
    """用户说「搞定了」→ 消解最近的 followup 托付与惦记。"""
    return bool(_DONE_RE.search(str(text or "")))


def backoff_seconds(tries: int) -> float:
    """指数退避：30s → 60s → 120s → 240s → 上限 300s。"""
    return min(MAX_BACKOFF_S, 30.0 * (2 ** max(0, int(tries))))


def describe_task(task: TaskSpec) -> str:
    """命令面板/气泡里的人话状态。"""
    state = {"pending": "进行中", "done": "已完成",
             "failed": "已停止", "cancelled": "已取消"}.get(task.status, task.status)
    tries = f"，已试 {task.tries} 次" if task.tries else ""
    return f"[{state}] {task.text}{tries}"


def is_safe_read_target(path: str, base_dir: str = "") -> bool:
    """只读沙箱的静态判定：不接受空路径、不接受目录通配之外的危险写法。

    真正的边界在执行层（非文件/符号链接/超上限一律拒绝），这里先挡住
    明显不该读的东西，避免把「任意文件读取」包装成功能。
    """
    candidate = str(path or "").strip()
    if not candidate or "\x00" in candidate:
        return False
    return not (candidate.startswith("-") or "://" in candidate)
