"""存在感仓储：~/.WisePet/presence.json——精确的「上次出现」时间与推送去重。

刻意独立于 pet_state.json（启动即被 last_updated 覆盖）、telemetry/growth（按天且
构造期就写今天）：小时级「久别重逢」必须有一个不被启动流程冲掉的精确时间戳。
"""
from __future__ import annotations

import logging
from pathlib import Path

from .json_store import atomic_write, load_json_or_backup_reset

logger = logging.getLogger(__name__)

FILE_NAME = "presence.json"


class PresenceStore:
    def __init__(self, data_dir: Path | str):
        self._path = Path(data_dir) / FILE_NAME

    def load(self) -> dict:
        data = load_json_or_backup_reset(self._path) or {}
        return {
            "last_seen_ts": float(data.get("last_seen_ts") or 0.0),
            "last_recall_day": str(data.get("last_recall_day") or ""),
            "last_reunion_day": str(data.get("last_reunion_day") or ""),
            # 惦记清单：已提过 / 已消解的指纹（按条去重，不再只记「今天提过没」）
            "recalled_keys": [str(x) for x in (data.get("recalled_keys") or [])][-50:],
            "resolved_recalls": [str(x) for x in (data.get("resolved_recalls") or [])][-50:],
        }

    def save(self, payload: dict) -> None:
        try:
            atomic_write(self._path, dict(payload))
        except OSError:
            logger.exception("presence save failed")

    def touch_seen(self, ts: float) -> None:
        data = self.load()
        data["last_seen_ts"] = float(ts)
        self.save(data)

    def mark_recall(self, day: str) -> None:
        data = self.load()
        data["last_recall_day"] = day
        self.save(data)

    def mark_reunion(self, day: str) -> None:
        data = self.load()
        data["last_reunion_day"] = day
        self.save(data)

    def mark_recall_key(self, key: str) -> None:
        data = self.load()
        keys = [x for x in data["recalled_keys"] if x != key]
        keys.append(key)
        data["recalled_keys"] = keys[-50:]
        self.save(data)

    def resolve_recall(self, key: str) -> None:
        """用户说「搞定了」：这件事以后不再提起。"""
        data = self.load()
        if key not in data["resolved_recalls"]:
            data["resolved_recalls"].append(key)
        data["recalled_keys"] = [x for x in data["recalled_keys"] if x != key]
        self.save(data)

    def excluded_keys(self) -> frozenset[str]:
        data = self.load()
        return frozenset(data["recalled_keys"] + data["resolved_recalls"])
