"""提醒持久化仓储：~/.WisePet/reminders.json（v2.1 提醒重启恢复）。"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .json_store import atomic_write

logger = logging.getLogger(__name__)


class ReminderStore:
    def __init__(self, data_dir: Path | str):
        self._path = Path(data_dir) / "reminders.json"

    def load_entries(self) -> list[dict]:
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            items = data.get("items", []) if isinstance(data, dict) else []
            return [it for it in items if isinstance(it, dict)]
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            logger.warning("reminders file corrupted, starting empty")
            return []

    def save_entries(self, items: list[dict]) -> None:
        atomic_write(self._path, {"items": list(items)})
