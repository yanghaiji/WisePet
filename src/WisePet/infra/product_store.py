"""产品层本地仓储：打扰预算计数 / 长期事实记忆（都只落 ~/.WisePet，不上传）。"""
from __future__ import annotations

import logging
from pathlib import Path

from .json_store import atomic_write, load_json_or_backup_reset

logger = logging.getLogger(__name__)

QUIET_FILE_NAME = "quiet.json"
MEMORY_FILE_NAME = "memory.json"


class QuietStore:
    """按天记录推送次数：{"day": "YYYY-MM-DD", "total": n, "by_source": {..}}。"""

    def __init__(self, data_dir: Path | str):
        self._path = Path(data_dir) / QUIET_FILE_NAME

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict:
        return load_json_or_backup_reset(self._path) or {}

    def save(self, data: dict) -> None:
        try:
            atomic_write(self._path, data)
        except OSError:
            logger.exception("quiet budget save failed")


class MemoryStore:
    """长期事实：只存用户主动说过的名字 / 正在做的事 / 项目名。"""

    _KEYS = ("user_name", "activity", "project")

    def __init__(self, data_dir: Path | str):
        self._path = Path(data_dir) / MEMORY_FILE_NAME

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, str]:
        data = load_json_or_backup_reset(self._path) or {}
        return {key: str(data.get(key) or "").strip()[:40] for key in self._KEYS}

    def save(self, facts: dict[str, str]) -> None:
        try:
            atomic_write(self._path,
                         {key: str(facts.get(key) or "")[:40] for key in self._KEYS})
        except OSError:
            logger.exception("long-term memory save failed")
