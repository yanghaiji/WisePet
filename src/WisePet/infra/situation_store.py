"""近期处境仓储：~/.WisePet/situation.json（原子写 + 损坏备份）。"""
from __future__ import annotations

import logging
from pathlib import Path

from .json_store import atomic_write, load_json_or_backup_reset

logger = logging.getLogger(__name__)

FILE_NAME = "situation.json"
MAX_RECORDS = 20


class SituationStore:
    def __init__(self, data_dir: Path | str):
        self._path = Path(data_dir) / FILE_NAME

    def load(self) -> list[dict]:
        data = load_json_or_backup_reset(self._path)
        records = data.get("situations") if isinstance(data, dict) else None
        return [dict(x) for x in records if isinstance(x, dict)][:MAX_RECORDS] \
            if isinstance(records, list) else []

    def save(self, records: list[dict]) -> None:
        try:
            atomic_write(self._path, {"situations": list(records)[:MAX_RECORDS]})
        except OSError:
            logger.exception("situation save failed")
