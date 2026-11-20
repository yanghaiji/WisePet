"""产品层本地仓储：打扰预算计数 / 长期事实记忆（都只落 ~/.WisePet，不上传）。"""
from __future__ import annotations

import logging
from pathlib import Path

from .json_store import atomic_write, load_json_or_backup_reset

logger = logging.getLogger(__name__)

QUIET_FILE_NAME = "quiet.json"
MEMORY_FILE_NAME = "memory.json"
AUDIT_FILE_NAME = "actions.json"


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
    """长期事实：只存用户主动说过的名字 / 正在做的事 / 项目名 + 变更时间线。"""

    _KEYS = ("user_name", "activity", "project")

    def __init__(self, data_dir: Path | str):
        self._path = Path(data_dir) / MEMORY_FILE_NAME

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, str]:
        data = load_json_or_backup_reset(self._path) or {}
        return {key: str(data.get(key) or "").strip()[:40] for key in self._KEYS}

    def load_history(self) -> list[dict]:
        data = load_json_or_backup_reset(self._path) or {}
        raw = data.get("history")
        return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []

    def save(self, facts: dict[str, str], history: list[dict] | None = None) -> None:
        payload = {key: str(facts.get(key) or "")[:40] for key in self._KEYS}
        if history is not None:
            payload["history"] = history[-50:]
        # 自由事实（v1.31）与三键同文件：整体覆写时必须带上，否则会被抹掉
        existing = load_json_or_backup_reset(self._path) or {}
        if isinstance(existing, dict) and isinstance(existing.get("fact_entries"), list):
            payload["fact_entries"] = existing["fact_entries"]
        try:
            atomic_write(self._path, payload)
        except OSError:
            logger.exception("long-term memory save failed")

    def delete_fact(self, key: str) -> bool:
        """按条遗忘一个事实，同时抹掉时间线里同 key 的记录。"""
        if key not in self._KEYS:
            return False
        facts = self.load()
        if not facts.get(key):
            return False
        facts[key] = ""
        history = [item for item in self.load_history()
                   if str(item.get("key") or "") != key]
        self.save(facts, history)
        return True

    def delete_history_at(self, index: int) -> bool:
        """按位置删除一条记忆时间线（事实本身保留）。"""
        history = self.load_history()
        if not 0 <= index < len(history):
            return False
        del history[index]
        self.save(self.load(), history)
        return True

    # ---------- 自由事实（v1.31 实体记忆泛化） ----------

    _FACT_KEY_LIMIT = 24
    _FACT_VALUE_LIMIT = 120
    MAX_FACTS = 50

    #: 自由事实的扩展字段（相关性检索/遗忘/修正用），旧数据缺这些字段时给默认值
    _FACT_EXTRA = ("tags", "source", "superseded_by", "last_accessed",
                   "access_count", "importance")

    def load_fact_entries(self) -> list[dict]:
        data = load_json_or_backup_reset(self._path) or {}
        raw = data.get("fact_entries")
        if not isinstance(raw, list):
            return []
        items = []
        for item in raw[:self.MAX_FACTS]:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()[:self._FACT_KEY_LIMIT]
            value = str(item.get("value") or "").strip()[:self._FACT_VALUE_LIMIT]
            if not key or not value:
                continue
            entry = {"key": key, "value": value,
                     "day": str(item.get("day") or "")}
            tags = item.get("tags")
            entry["tags"] = ([str(t) for t in tags if str(t).strip()]
                             if isinstance(tags, list) else [])
            entry["source"] = str(item.get("source") or "user")
            entry["superseded_by"] = str(item.get("superseded_by") or "")
            entry["last_accessed"] = str(item.get("last_accessed") or entry["day"])
            try:
                entry["access_count"] = int(item.get("access_count") or 0)
            except (TypeError, ValueError):
                entry["access_count"] = 0
            try:
                entry["importance"] = float(item.get("importance") or 1.0)
            except (TypeError, ValueError):
                entry["importance"] = 1.0
            items.append(entry)
        return items

    def save_fact_entries(self, entries: list[dict]) -> None:
        payload = load_json_or_backup_reset(self._path) or {}
        if not isinstance(payload, dict):
            payload = {}
        for key in self._KEYS:                      # 三键字段不能丢
            payload[key] = payload.get(key, "")
        payload["fact_entries"] = list(entries)[:self.MAX_FACTS]
        history = payload.get("history")
        if isinstance(history, list):
            payload["history"] = history[-50:]
        try:
            atomic_write(self._path, payload)
        except OSError:
            logger.exception("long-term fact entries save failed")

    def delete_fact_entry(self, index: int) -> bool:
        entries = self.load_fact_entries()
        if not 0 <= index < len(entries):
            return False
        del entries[index]
        self.save_fact_entries(entries)
        return True


class ActionAuditStore:
    """工具执行历史：只追加最近若干条，供命令面板「最近做了什么」回看。"""

    def __init__(self, data_dir: Path | str):
        self._path = Path(data_dir) / AUDIT_FILE_NAME

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> list[dict]:
        data = load_json_or_backup_reset(self._path) or {}
        items = data.get("items")
        return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []

    def append(self, entry: dict) -> None:
        items = (self.load() + [entry])[-50:]
        try:
            atomic_write(self._path, {"items": items})
        except OSError:
            logger.exception("action audit save failed")

    def clear(self) -> None:
        try:
            atomic_write(self._path, {"items": []})
        except OSError:
            logger.exception("action audit clear failed")

    def delete_at(self, index: int) -> bool:
        """按条目位置删除一条审计记录（时间线「按条遗忘」）。"""
        items = self.load()
        if not 0 <= index < len(items):
            return False
        del items[index]
        atomic_write(self._path, {"items": items})
        return True
