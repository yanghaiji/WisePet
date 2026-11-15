"""托付任务仓储：~/.WisePet/tasks.json（跨重启存活）。"""
from __future__ import annotations

import logging
from pathlib import Path

from .json_store import atomic_write, load_json_or_backup_reset

logger = logging.getLogger(__name__)

FILE_NAME = "tasks.json"
#: 历史任务保留上限（已完成的也留着，用户能在时间线里看到做过什么）
MAX_KEEP = 100


class TaskStore:
    def __init__(self, data_dir: Path | str):
        self._path = Path(data_dir) / FILE_NAME

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> list[dict]:
        data = load_json_or_backup_reset(self._path) or {}
        items = data.get("items") if isinstance(data, dict) else None
        return [it for it in (items or []) if isinstance(it, dict)][:MAX_KEEP]

    def save(self, items: list[dict]) -> None:
        try:
            atomic_write(self._path, {"items": list(items)[:MAX_KEEP]})
        except OSError:
            logger.exception("task store save failed")
