"""TaskService：把「一次性动作」升级成「可托付的事」。

- watch_file：盯着一个本机文件，出现关键词才开口；只读、限大小、拒符号链接
- followup：按间隔提醒，用户说「搞定了」自动消解；超过次数就体面地停
- 失败一律按退避重试并如实说明，绝不静默消失；状态落 tasks.json 跨重启存活

所有推送经唯一 SpeakRequestedEvent 出口，照旧受打扰预算 / 免打扰 / 勿扰约束。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from ..domain.events import SpeakRequestedEvent
from ..domain.task_protocol import (
    FOLLOWUP, MAX_FILE_BYTES, WATCH_FILE, WATCH_PAGE, TaskSpec, backoff_seconds,
    describe_task, is_done_signal, is_safe_read_target, is_safe_watch_url,
    page_changed,
)

logger = logging.getLogger(__name__)


class TaskService:
    def __init__(self, clock, bus, config_provider: Callable[[], object], store,
                 audit=None, enabled_provider: Callable[[], bool] | None = None,
                 page_fetcher: Callable[[str], str] | None = None):
        self._clock = clock
        self._bus = bus
        self._config = config_provider
        self._store = store
        self._audit = audit
        self._enabled = enabled_provider
        self._page_fetcher = page_fetcher   # WATCH_PAGE 用：url -> 页面文本
        self._tasks: list[TaskSpec] = [
            TaskSpec.from_dict(item) for item in self._store.load()
            if str(item.get("id") or "")]

    # ---------- 查询 ----------

    def tasks(self, *, include_finished: bool = False) -> list[TaskSpec]:
        if include_finished:
            return list(self._tasks)
        return [t for t in self._tasks if t.status == "pending"]

    def describe(self, *, include_finished: bool = True) -> str:
        items = self.tasks(include_finished=include_finished)
        return "\n".join(describe_task(t) for t in items) if items else "还没有托付给我的事～"

    # ---------- 命令 ----------

    def add(self, spec: TaskSpec) -> bool:
        if len(self.tasks()) >= 20:
            self._publish("手上托付太多啦，先取消几个再交给我吧～", "托付太多")
            return False
        self._tasks = [t for t in self._tasks if t.id != spec.id]
        self._tasks.append(spec)
        self._save()
        self._publish(f"好，{spec.text}，我替你盯着。说「搞定了」我就停。", "接下托付")
        if self._audit is not None:
            self._audit.record("task_add", spec.kind, "ok")
        logger.info("task added: %s %s", spec.kind, spec.text)
        return True

    def cancel(self, task_id: str) -> bool:
        for index, task in enumerate(self._tasks):
            if task.id == task_id and task.status == "pending":
                self._tasks[index] = self._patch(task, status="cancelled")
                self._save()
                return True
        return False

    def cancel_all(self) -> int:
        count = 0
        for index, task in enumerate(self._tasks):
            if task.status == "pending":
                self._tasks[index] = self._patch(task, status="cancelled")
                count += 1
        if count:
            self._save()
        return count

    def delete(self, task_id: str) -> bool:
        """时间线「按条遗忘」：从历史里彻底抹掉一条托付。"""
        before = len(self._tasks)
        self._tasks = [t for t in self._tasks if t.id != task_id]
        if len(self._tasks) != before:
            self._save()
            return True
        return False

    def resolve(self, text: str) -> int:
        """用户说「搞定了」：消解所有 followup 托付（watch_file 不受影响）。"""
        if not is_done_signal(text):
            return 0
        count = 0
        for index, task in enumerate(self._tasks):
            if task.kind == FOLLOWUP and task.status == "pending":
                self._tasks[index] = self._patch(task, status="done")
                count += 1
        if count:
            self._save()
            logger.info("tasks resolved by done signal: %d", count)
        return count

    # ---------- 周期驱动 ----------

    def tick(self, now: float | None = None) -> None:
        now = self._clock.now() if now is None else now
        if self._enabled is not None and not self._enabled():
            return
        due = [t for t in self._tasks
               if t.status == "pending" and t.next_at and t.next_at <= now]
        if not due:
            return
        changed = False
        for task in due:
            if task.kind == WATCH_FILE:
                changed = self._run_watch(task, now) or changed
            elif task.kind == WATCH_PAGE:
                changed = self._run_watch_page(task, now) or changed
            elif task.kind == FOLLOWUP:
                changed = self._run_followup(task, now) or changed
        if changed:
            self._save()

    # ---------- 两类执行 ----------

    def _run_watch(self, task: TaskSpec, now: float) -> bool:
        index = self._index(task.id)
        if index < 0:
            return False
        try:
            hits = self._read_and_match(task)
        except (OSError, ValueError) as exc:
            return self._fail(task, index, now, str(exc))
        if hits:
            self._publish(f"你让我盯的 {task.target} 里出现了「{task.match}」！",
                          "我盯着的事有动静了")
            self._tasks[index] = self._patch(task, status="done", tries=task.tries + 1)
            if self._audit is not None:
                self._audit.record("task_watch", task.target, "matched")
            return True
        self._tasks[index] = self._patch(task, next_at=now + max(30.0, task.interval_s))
        return True

    def _run_watch_page(self, task: TaskSpec, now: float) -> bool:
        index = self._index(task.id)
        if index < 0:
            return False
        if self._page_fetcher is None or not is_safe_watch_url(task.target):
            return self._fail(task, index, now, "网页地址不可用或不安全")
        try:
            text = str(self._page_fetcher(task.target) or "")
        except Exception as exc:  # noqa: BLE001 - 抓取失败走退避
            return self._fail(task, index, now, str(exc))
        meta = dict(task.meta or {})
        previous = str(meta.get("baseline") or "")
        keyword = str(task.match or meta.get("keyword") or "")
        # 首抓只记基线不出声；之后关键词出现或内容变化才告诉用户
        if previous and page_changed(previous, text, keyword):
            self._publish(f"你让我盯的网页{'出现「'+keyword+'」' if keyword else '有变化'}啦，快去看看～",
                          "我盯着的事有动静了")
            if keyword:   # 关键词命中即停；纯变化监控继续盯
                self._tasks[index] = self._patch(task, status="done",
                                                 tries=task.tries + 1)
                if self._audit is not None:
                    self._audit.record("task_watch_page", task.target, "matched")
                return True
        meta["baseline"] = text[:20000]   # 存一段文本作下次对比基线（限长防膨胀）
        self._tasks[index] = self._patch(
            task, meta=meta, next_at=now + max(60.0, task.interval_s))
        return True

    def _run_followup(self, task: TaskSpec, now: float) -> bool:
        index = self._index(task.id)
        if index < 0:
            return False
        tries = task.tries + 1
        self._publish(f"还惦记着呢：{task.text.split('：')[-1]}。搞定了就跟我说一声～",
                      "还惦记着呢")
        if task.max_tries and tries >= task.max_tries:
            self._tasks[index] = self._patch(task, status="done", tries=tries)
            self._publish("这件事我就不催啦，需要时再叫我。", "托付收尾")
        else:
            self._tasks[index] = self._patch(
                task, tries=tries, next_at=now + max(30.0, task.interval_s))
        return True

    def _read_and_match(self, task: TaskSpec) -> bool:
        """只读沙箱：普通文件、非符号链接、大小上限、只做子串匹配。

        不执行文件、不解析代码、不写回；路径由用户在自己的话里显式给出。
        """
        if not is_safe_read_target(task.target):
            raise ValueError("路径不合法")
        path = Path(task.target)
        if path.is_symlink() or not path.is_file():
            raise ValueError("不是可读的普通文件")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("文件太大，不读")
        content = path.read_text(encoding="utf-8", errors="replace")
        return str(task.match) in content

    def _fail(self, task: TaskSpec, index: int, now: float, reason: str) -> bool:
        tries = task.tries + 1
        if task.max_tries and tries >= task.max_tries:
            self._tasks[index] = self._patch(task, status="failed", tries=tries,
                                             last_error=reason[:120])
            self._publish(f"托付没能继续（{reason[:40]}），我先停下了，"
                          f"想重试再跟我说一次。", "托付停下了")
            logger.warning("task failed: %s %s", task.text, reason)
        else:
            delay = backoff_seconds(tries)
            self._tasks[index] = self._patch(
                task, tries=tries, next_at=now + delay, last_error=reason[:120])
            logger.info("task retry in %.0fs: %s", delay, reason[:60])
        return True

    # ---------- 内部 ----------

    def _index(self, task_id: str) -> int:
        for index, task in enumerate(self._tasks):
            if task.id == task_id:
                return index
        return -1

    @staticmethod
    def _patch(task: TaskSpec, **changes) -> TaskSpec:
        data = task.to_dict()
        data.update(changes)
        return TaskSpec.from_dict(data)

    def _publish(self, text: str, title: str) -> None:
        self._bus.publish(SpeakRequestedEvent(text, source="cheer", title=title))

    def _save(self) -> None:
        self._store.save([t.to_dict() for t in self._tasks])
