"""周报推送标记仓储：~/.WisePet/weekly.json（记录最近一次已推送的自然周）。"""
from __future__ import annotations

import logging
from pathlib import Path

from .json_store import atomic_write, load_json_or_backup_reset

logger = logging.getLogger(__name__)

WEEKLY_FILE_NAME = "weekly.json"


class WeeklyStore:
    def __init__(self, data_dir: Path | str):
        self._path = Path(data_dir) / WEEKLY_FILE_NAME

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict:
        data = load_json_or_backup_reset(self._path)
        return data if isinstance(data, dict) else {}

    def save(self, marker: dict) -> None:
        try:
            atomic_write(self._path, dict(marker or {}))
        except OSError:
            logger.exception("weekly marker save failed")
