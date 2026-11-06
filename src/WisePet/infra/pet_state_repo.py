"""宠物状态快照仓储：~/.WisePet/pet_state.json（PRD US-4 离线恢复）。"""
from __future__ import annotations

import logging
from pathlib import Path

from ..domain.pet import Pet
from .json_store import atomic_write, load_json_or_backup_reset

logger = logging.getLogger(__name__)

STATE_FILE_NAME = "pet_state.json"


class PetStateRepository:
    def __init__(self, data_dir: Path | str | None = None):
        from .config_repo import default_data_dir
        self._dir = Path(data_dir) if data_dir else default_data_dir()

    @property
    def path(self) -> Path:
        return self._dir / STATE_FILE_NAME

    def load(self) -> Pet | None:
        data = load_json_or_backup_reset(self.path)
        if data is None:
            return None
        try:
            return Pet.from_snapshot(data)
        except (TypeError, ValueError):
            logger.exception("invalid pet state snapshot: %s", data)
            return None

    def save(self, pet: Pet) -> None:
        atomic_write(self.path, pet.snapshot())
