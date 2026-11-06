"""成长数据仓储：~/.WisePet/growth.json（累计积分 / 陪伴天数 / 各类互动计数）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .json_store import atomic_write, load_json_or_backup_reset

logger = logging.getLogger(__name__)

GROWTH_FILE_NAME = "growth.json"


@dataclass
class GrowthData:
    points: int = 0
    companion_days: int = 0
    last_day: str = ""     # 已计入陪伴的最近日期 YYYY-MM-DD
    fed: int = 0
    petted: int = 0
    chatted: int = 0

    def to_dict(self) -> dict:
        return {"points": self.points, "companion_days": self.companion_days,
                "last_day": self.last_day, "fed": self.fed,
                "petted": self.petted, "chatted": self.chatted}

    @classmethod
    def from_dict(cls, data: dict) -> "GrowthData":
        d = data if isinstance(data, dict) else {}
        return cls(
            points=_int(d.get("points")),
            companion_days=_int(d.get("companion_days")),
            last_day=str(d.get("last_day") or ""),
            fed=_int(d.get("fed")),
            petted=_int(d.get("petted")),
            chatted=_int(d.get("chatted")),
        )


class GrowthStore:
    def __init__(self, data_dir: Path | str):
        self._path = Path(data_dir) / GROWTH_FILE_NAME

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> GrowthData:
        data = load_json_or_backup_reset(self._path)
        if data is None:
            return GrowthData()
        return GrowthData.from_dict(data)

    def save(self, growth: GrowthData) -> None:
        try:
            atomic_write(self._path, growth.to_dict())
        except OSError:
            logger.exception("growth save failed")


def _int(raw) -> int:
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0
