"""按天 JSON 聊天记录仓储：旧格式的读取/回退工具，运行时不再装配。

FR v1.22 / NFR v1.23 起，聊天记录统一走 `sqlite_chat_repo.SqliteChatRepository`
（~/.WisePet/wisepet.db）。保留本类是为了能读回旧 `chat/YYYY-MM-DD.json`、
留一条可回退路径，并守住原 FR v1.8 的按天文件契约测试。
复用 `json_store` 的原子写与损坏备份；文件顶层为 dict，符合其载荷契约。
"""
from __future__ import annotations

import logging
from pathlib import Path

from ..domain.chat_record import ChatRecord, day_key
from .json_store import atomic_write, load_json_or_backup_reset

logger = logging.getLogger(__name__)

CHAT_DIR_NAME = "chat"


class ChatLogRepository:
    def __init__(self, data_dir: Path | str | None = None):
        from .config_repo import default_data_dir
        self._dir = Path(data_dir) if data_dir else default_data_dir()

    @property
    def directory(self) -> Path:
        return self._dir / CHAT_DIR_NAME

    def path_for(self, day: str) -> Path:
        return self.directory / f"{day}.json"

    def append(self, record: ChatRecord) -> None:
        """按记录自身时间戳归档到当天文件；同目录原子替换，不留半截 JSON。"""
        day = day_key(record.ts or None)
        path = self.path_for(day)
        data = load_json_or_backup_reset(path) or {"date": day, "records": []}
        records = list(data.get("records") or [])
        records.append(record.to_dict())
        atomic_write(path, {"date": day, "records": records})

    def load(self, day: str) -> list[ChatRecord]:
        data = load_json_or_backup_reset(self.path_for(day))
        if not data:
            return []
        return [ChatRecord.from_dict(item) for item in (data.get("records") or [])
                if isinstance(item, dict)]

    def days(self) -> list[str]:
        """已有记录文件日期（升序），供以后按天回看用。"""
        if not self.directory.is_dir():
            return []
        return sorted(p.stem for p in self.directory.glob("*.json"))
