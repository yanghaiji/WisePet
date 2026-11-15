"""MemoryTimeline：把「它知道我什么」变成一页可读、可勾选、可删除的时间线。

聚合本机全部数据文件中的可识别条目（长期记忆事实 / 记忆变更 / 按天聊天 /
工具执行历史 / 托付 / 资料索引 / 埋点 / 惦记标记），每条带来源与唯一 id；
`forget(ids)` 分派到各自仓储做**真正的删除**，不是隐藏。

设计约束：所有依赖可选注入，任何一个缺失就跳过对应来源（便于单测与降级）；
删除后同步 reload 内存态，避免 UI 显示与运行时不一致。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TimelineEntry:
    id: str
    source: str
    day: str
    text: str
    removable: bool = True

    def line(self) -> str:
        return f"[{self.source}] {self.day}  {self.text}"


class MemoryTimeline:
    def __init__(self, *, memory_store=None, memory_service=None, audit_store=None,
                 chat_repo=None, telemetry=None, presence_store=None,
                 task_service=None, knowledge=None, event_store=None):
        self._memory = memory_store
        self._memory_service = memory_service
        self._audit = audit_store
        self._chat = chat_repo
        self._telemetry = telemetry
        self._presence = presence_store
        self._tasks = task_service
        self._knowledge = knowledge
        self._events = event_store

    # ---------- 聚合 ----------

    def entries(self) -> list[TimelineEntry]:
        items: list[TimelineEntry] = []
        self._collect_memory(items)
        self._collect_history(items)
        self._collect_chats(items)
        self._collect_audit(items)
        self._collect_tasks(items)
        self._collect_singletons(items)
        return items

    def _collect_memory(self, items: list[TimelineEntry]) -> None:
        if self._memory is None:
            return
        try:
            for key, value in self._memory.load().items():
                if value:
                    items.append(TimelineEntry(
                        id=f"memory:{key}", source="长期记忆", day="—",
                        text=f"{key} = {value}"))
            for index, entry in enumerate(self._memory.load_fact_entries()):
                items.append(TimelineEntry(
                    id=f"memory_fact:{index}", source="记住的事",
                    day=str(entry.get("day") or "—"),
                    text=f"{entry.get('key')}：{entry.get('value')}"))
        except OSError:
            logger.debug("timeline memory scan failed", exc_info=True)

    def _collect_history(self, items: list[TimelineEntry]) -> None:
        if self._memory is None:
            return
        try:
            for index, item in enumerate(self._memory.load_history()):
                items.append(TimelineEntry(
                    id=f"history:{index}", source="记忆变更",
                    day=str(item.get("day") or "—"),
                    text=f"{item.get('key')} = {item.get('value')}"))
        except OSError:
            logger.debug("timeline history scan failed", exc_info=True)

    def _collect_chats(self, items: list[TimelineEntry]) -> None:
        if self._chat is None:
            return
        try:
            for day in self._chat.days():
                items.append(TimelineEntry(
                    id=f"chat:{day}", source="聊天记录", day=day,
                    text=f"{day} 的全部对话（{len(self._chat.load(day))} 条）"))
        except OSError:
            logger.debug("timeline chat scan failed", exc_info=True)

    def _collect_audit(self, items: list[TimelineEntry]) -> None:
        if self._audit is None:
            return
        try:
            for index, entry in enumerate(self._audit.load()):
                items.append(TimelineEntry(
                    id=f"audit:{index}", source="工具执行",
                    day=str(entry.get("day") or "—"),
                    text=f"{entry.get('kind')} → {entry.get('target')}"
                         f"（{entry.get('result')}）"))
        except OSError:
            logger.debug("timeline audit scan failed", exc_info=True)

    def _collect_tasks(self, items: list[TimelineEntry]) -> None:
        if self._tasks is None:
            return
        for task in self._tasks.tasks(include_finished=True):
            items.append(TimelineEntry(
                id=f"task:{task.id}", source="托付", day="—",
                text=f"[{task.status}] {task.text}"))

    def _collect_singletons(self, items: list[TimelineEntry]) -> None:
        if self._knowledge is not None:
            status = self._knowledge.status()
            if status.get("chunks"):
                items.append(TimelineEntry(
                    id="knowledge", source="资料索引", day="—",
                    text=f"{status.get('folder')}（{status.get('files')} 文件 / "
                         f"{status.get('chunks')} 片段）"))
        if self._telemetry is not None:
            items.append(TimelineEntry(
                id="telemetry", source="本地埋点", day="—",
                text="按天计数的功能使用指标（不含消息内容）"))
        if self._presence is not None:
            try:
                state = self._presence.load()
                if state.get("last_recall_day") or state.get("resolved_recalls"):
                    items.append(TimelineEntry(
                        id="presence", source="惦记标记", day="—",
                        text="上次出现时间 / 已提过的惦记 / 已消解的惦记"))
            except OSError:
                pass
        if self._events is not None:
            items.append(TimelineEntry(
                id="events", source="事件去重", day="—",
                text="今日已触发的情境规则"))

    # ---------- 遗忘 ----------

    def forget(self, ids: list[str]) -> int:
        """真正的删除；返回成功删掉的条数。未知 id 静默忽略。"""
        done = 0
        # 审计按索引删除要倒序，否则前面的删掉会让后面的索引错位
        audit_indices = sorted(
            (int(item.split(":", 1)[1]) for item in ids
             if item.startswith("audit:") and item.split(":", 1)[1].isdigit()),
            reverse=True)
        history_indices = sorted(
            (int(item.split(":", 1)[1]) for item in ids
             if item.startswith("history:") and item.split(":", 1)[1].isdigit()),
            reverse=True)
        # 索引型条目先删：删事实会连带清掉同 key 的历史，顺序反了索引就错位
        for index in audit_indices:
            if self._audit is not None and self._audit.delete_at(index):
                done += 1
        for index in history_indices:
            if self._memory is not None and self._memory.delete_history_at(index):
                done += 1
        for item_id in ids:
            try:
                done += self._forget_one(item_id)
            except (OSError, ValueError, IndexError):
                logger.warning("timeline forget failed: %s", item_id, exc_info=True)
        if (self._memory_service is not None
                and any(i.startswith(("memory:", "history:", "memory_fact:"))
                        for i in ids)):
            self._memory_service.reload()
        return done

    def _forget_one(self, item_id: str) -> int:
        if item_id.startswith("memory_fact:"):
            tail = item_id.split(":", 1)[1]
            if tail.isdigit() and self._memory is not None:
                return int(bool(self._memory.delete_fact_entry(int(tail))))
            return 0
        if item_id.startswith("memory:"):
            return int(bool(self._memory and self._memory.delete_fact(
                item_id.split(":", 1)[1])))
        if item_id.startswith("chat:"):
            return int(bool(self._chat and self._chat.delete_day(
                item_id.split(":", 1)[1])))
        if item_id.startswith("task:"):
            return int(bool(self._tasks and self._tasks.delete(
                item_id.split(":", 1)[1])))
        if item_id == "knowledge" and self._knowledge is not None:
            self._knowledge.clear()
            return 1
        if item_id == "telemetry" and self._telemetry is not None:
            self._telemetry.clear()
            return 1
        if item_id == "presence" and self._presence is not None:
            state = self._presence.load()
            state["last_recall_day"] = ""
            state["resolved_recalls"] = []
            state["recalled_keys"] = []
            self._presence.save(state)
            return 1
        if item_id == "events" and self._events is not None:
            self._events.clear()
            return 1
        return 0

    def export_text(self) -> str:
        """导出为纯文本（本机 Markdown 友好），不含 API Key 与文件路径以外的内容。"""
        lines = ["# WisePet 本机数据时间线", ""]
        lines += [entry.line() for entry in self.entries()]
        return "\n".join(lines) + "\n"
