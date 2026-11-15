"""事件触发去重仓储：~/.WisePet/events.json（按自然日记录已触发规则）。"""
from __future__ import annotations

import logging
from pathlib import Path

from .json_store import atomic_write, load_json_or_backup_reset

logger = logging.getLogger(__name__)

FILE_NAME = "events.json"


class EventStore:
    def __init__(self, data_dir: Path | str):
        self._path = Path(data_dir) / FILE_NAME

    def load(self) -> dict:
        data = load_json_or_backup_reset(self._path) or {}
        if not isinstance(data, dict):
            return {}
        fired = data.get("fired") or []
        return {"day": str(data.get("day") or ""),
                "fired": [str(x) for x in fired]}

    def save(self, payload: dict) -> None:
        try:
            atomic_write(self._path, dict(payload))
        except OSError:
            logger.exception("event store save failed")

    def clear(self) -> None:
        try:
            if self._path.exists():
                self._path.unlink()
        except OSError:
            logger.exception("event store clear failed")
