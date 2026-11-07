"""聊天记录仓储（SQLite）：~/.WisePet/wisepet.db，单文件、真追加、可按天/全文检索。

为什么只迁聊天记录：config / pet_state / growth / quiet / memory / weekly / reminders /
focus 都是 KB 级、单写者、事件驱动的小文档，保持 JSON 可读可手改；只有聊天记录是
「持续增长 + 语义只追加 + 需要跨日检索」，旧的 append 是「读全量→追加→整文件重写」的
O(n²)，换库收益全在这里。sqlite3 是标准库，不引入任何新依赖。

旧 `chat/YYYY-MM-DD.json` 在首次启动时一次性导入，成功后归档到
`chat/legacy/*.json.migrated`（**不删除，删掉 db 即可回退**）。
"""
from __future__ import annotations

import logging
import shutil
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from ..domain.chat_record import ChatRecord, day_key
from .json_store import load_json_or_backup_reset

logger = logging.getLogger(__name__)

CHAT_DB_NAME = "wisepet.db"
CHAT_DIR_NAME = "chat"
LEGACY_DIR_NAME = "legacy"
LEGACY_SUFFIX = ".migrated"

_FIELDS = "role, content, source, ts, session_id"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    ts REAL NOT NULL DEFAULT 0,
    role TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    legacy_key TEXT UNIQUE            -- 迁移幂等：{原文件名}:{序号}
);
CREATE INDEX IF NOT EXISTS idx_chat_turns_day ON chat_turns(day, id);
CREATE INDEX IF NOT EXISTS idx_chat_turns_ts ON chat_turns(ts DESC);
"""


def _to_record(row) -> ChatRecord:
    return ChatRecord(role=row[0], content=row[1], source=row[2],
                      ts=float(row[3] or 0.0), session_id=row[4])


class SqliteChatRepository:
    """与旧 `ChatLogRepository` 同接口（append/load/days），额外提供 search()。"""

    def __init__(self, data_dir: Path | str | None = None):
        if data_dir is None:
            from .config_repo import default_data_dir
            data_dir = default_data_dir()
        self._dir = Path(data_dir)
        self._path = self._dir / CHAT_DB_NAME
        self._ensure_schema()
        self._migrate_legacy()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def directory(self) -> Path:
        """旧按天目录，现在只用于一次性迁移与归档。"""
        return self._dir / CHAT_DIR_NAME

    def path_for(self, day: str) -> Path:
        """兼容旧接口；仅迁移/回退工具会用，运行时不再产生按天 JSON。"""
        return self.directory / f"{day}.json"

    def _connect(self) -> sqlite3.Connection:
        # 不启用 WAL：单写者用不上，且 WAL 会额外产生 -wal/-shm 两个文件
        return sqlite3.connect(self._path, timeout=5.0)

    # ---------- 写 / 读 ----------

    def append(self, record: ChatRecord, day: str | None = None,
               legacy_key: str | None = None) -> None:
        """真追加一条 INSERT，不再读全量重写当天文件。"""
        day = day or day_key(record.ts or None)
        values = (day, float(record.ts or 0.0), record.role, record.content,
                  record.source, record.session_id)
        with closing(self._connect()) as conn:
            if legacy_key is None:
                conn.execute(
                    "INSERT INTO chat_turns(day,ts,role,content,source,session_id) "
                    "VALUES(?,?,?,?,?,?)", values)
            else:
                conn.execute(
                    "INSERT OR IGNORE INTO chat_turns"
                    "(day,ts,role,content,source,session_id,legacy_key) "
                    "VALUES(?,?,?,?,?,?,?)", values + (legacy_key,))
            conn.commit()

    def load(self, day: str) -> list[ChatRecord]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT {_FIELDS} FROM chat_turns WHERE day = ? ORDER BY id",
                (day,)).fetchall()
        return [_to_record(row) for row in rows]

    def days(self) -> list[str]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT DISTINCT day FROM chat_turns ORDER BY day").fetchall()
        return [row[0] for row in rows]

    def search(self, query: str, limit: int = 50) -> list[ChatRecord]:
        """跨日子串检索，大小写不敏感、按时间倒序；一次 SQL，不扫一堆按天文件。

        中文靠 instr 做子串匹配（FTS5 默认不切中文，这里用不上）。
        """
        needle = str(query or "").strip()
        if not needle or limit <= 0:
            return []
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT {_FIELDS} FROM chat_turns "
                "WHERE instr(lower(content), lower(?)) > 0 "
                "ORDER BY ts DESC, id DESC LIMIT ?", (needle, limit)).fetchall()
        return [_to_record(row) for row in rows]

    def count(self) -> int:
        with closing(self._connect()) as conn:
            return int(conn.execute("SELECT count(*) FROM chat_turns").fetchone()[0])

    def delete_day(self, day: str) -> int:
        """本地数据管家：按天彻底删除（对应旧「删某天 JSON 文件」的语义）。"""
        with closing(self._connect()) as conn:
            cursor = conn.execute("DELETE FROM chat_turns WHERE day = ?", (day,))
            conn.commit()
            return int(cursor.rowcount or 0)

    # ---------- 建表与损坏自愈 ----------

    def _ensure_schema(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        try:
            self._create_tables()
        except sqlite3.DatabaseError:
            logger.warning("chat db corrupted, backing up and rebuilding: %s", self._path)
            self._backup_corrupt_db()
            self._create_tables()

    def _create_tables(self) -> None:
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def _backup_corrupt_db(self) -> None:
        backup = self._dir / f"{CHAT_DB_NAME}.bak-{int(time.time())}"
        try:
            shutil.move(str(self._path), str(backup))
        except OSError:
            logger.exception("failed to backup corrupted chat db")
            self._path.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm"):
            (self._dir / f"{CHAT_DB_NAME}{suffix}").unlink(missing_ok=True)

    # ---------- 旧 JSON 一次性迁移 ----------

    def _migrate_legacy(self) -> int:
        source = self.directory
        if not source.is_dir():
            return 0
        files = sorted(source.glob("*.json"))
        if not files:
            return 0
        # db 里已经有数据就只归档不重复导入（用户把旧文件放回来也不会产生重复记录）
        already_seeded = self.count() > 0
        imported = 0
        for path in files:
            data = load_json_or_backup_reset(path)
            if data is None:
                continue                 # 损坏文件已由 json_store 改名备份，别归档
            if not already_seeded:
                day = str(data.get("date") or path.stem)
                for index, item in enumerate(data.get("records") or []):
                    if not isinstance(item, dict):
                        continue
                    self.append(ChatRecord.from_dict(item), day=day,
                                legacy_key=f"{path.stem}:{index}")
                    imported += 1
            self._archive(path)
        logger.info("legacy chat migrated: %d records from %d files", imported,
                    len(files))
        return imported

    def _archive(self, path: Path) -> None:
        legacy = path.parent / LEGACY_DIR_NAME
        legacy.mkdir(parents=True, exist_ok=True)
        target = legacy / f"{path.name}{LEGACY_SUFFIX}"
        if target.exists():
            target = legacy / f"{path.stem}-{int(time.time())}.json{LEGACY_SUFFIX}"
        try:
            path.rename(target)
        except OSError:
            logger.exception("failed to archive legacy chat file: %s", path)
