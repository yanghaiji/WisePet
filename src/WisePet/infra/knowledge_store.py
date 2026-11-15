"""本地资料索引仓储：~/.WisePet/knowledge.json（只存索引，不上传）。"""
from __future__ import annotations

import logging
from pathlib import Path

from .json_store import atomic_write, load_json_or_backup_reset

logger = logging.getLogger(__name__)

FILE_NAME = "knowledge.json"


class KnowledgeStore:
    def __init__(self, data_dir: Path | str):
        self._path = Path(data_dir) / FILE_NAME

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict:
        data = load_json_or_backup_reset(self._path) or {}
        if not isinstance(data, dict):
            return {}
        chunks = data.get("chunks") or []
        return {
            "folder": str(data.get("folder") or ""),
            "built_at": float(data.get("built_at") or 0.0),
            "files": int(data.get("files") or 0),
            "chunks": [c for c in chunks if isinstance(c, dict)],
        }

    def save(self, payload: dict) -> None:
        try:
            atomic_write(self._path, dict(payload))
        except OSError:
            logger.exception("knowledge index save failed")

    def clear(self) -> None:
        try:
            if self._path.exists():
                self._path.unlink()
        except OSError:
            logger.exception("knowledge index clear failed")
