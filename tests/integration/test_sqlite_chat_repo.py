"""聊天记录迁 SQLite：单文件、真追加、跨日检索、旧 JSON 一次性导入且可回滚。

其它持久化文件（config / pet_state / growth / ...）保持 JSON 不变，见最后一个用例。
"""
from __future__ import annotations

import json
import sqlite3

from WisePet.app.chat_history import ChatHistory
from WisePet.domain.chat_record import ChatRecord, day_key
from WisePet.infra.sqlite_chat_repo import CHAT_DB_NAME, SqliteChatRepository

BASE = 1_700_000_000.0


def _record(content: str, ts: float, role: str = "user",
            source: str = "user") -> ChatRecord:
    return ChatRecord(role=role, content=content, source=source,
                      ts=ts, session_id=day_key(ts))


def _legacy_file(data_dir, day: str, contents: list[str]) -> None:
    path = data_dir / "chat" / f"{day}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "date": day,
        "records": [{"role": "user", "content": text, "source": "user",
                     "ts": BASE + i, "session_id": day}
                    for i, text in enumerate(contents)],
    }, ensure_ascii=False), encoding="utf-8")


# ---------- 基本契约 ----------

def should_append_and_load_in_insertion_order(tmp_path):
    repo = SqliteChatRepository(tmp_path)
    repo.append(_record("你好", BASE))
    repo.append(_record("在呢", BASE + 60, role="assistant", source="local"))

    loaded = repo.load(day_key(BASE))
    assert [r.content for r in loaded] == ["你好", "在呢"]
    assert [r.source for r in loaded] == ["user", "local"]
    assert loaded[0].session_id == day_key(BASE)


def should_split_by_day_without_truncating(tmp_path):
    repo = SqliteChatRepository(tmp_path)
    for i in range(25):
        repo.append(_record(f"第{i}条", BASE + i * 60))
    repo.append(_record("另一天", BASE + 86400 * 3))

    assert len(repo.days()) == 2
    assert len(repo.load(day_key(BASE))) == 25
    assert len(repo.load(day_key(BASE + 86400 * 3))) == 1


def should_return_empty_for_unknown_day(tmp_path):
    repo = SqliteChatRepository(tmp_path)
    assert repo.load("2026-11-05") == []
    assert repo.days() == []


def should_keep_full_unicode_content(tmp_path):
    repo = SqliteChatRepository(tmp_path)
    repo.append(_record("今天心情不太好…<摸摸头>🐱", BASE))
    assert repo.load(day_key(BASE))[0].content == "今天心情不太好…<摸摸头>🐱"


def should_use_one_db_file_instead_of_daily_json(tmp_path):
    repo = SqliteChatRepository(tmp_path)
    repo.append(_record("一条", BASE))

    assert (tmp_path / CHAT_DB_NAME).is_file()
    assert not list((tmp_path / "chat").glob("*.json"))


# ---------- 检索（由 app 层委托，不再全量扫文件）----------

def should_search_case_insensitively_in_time_desc_order(tmp_path):
    repo = SqliteChatRepository(tmp_path)
    repo.append(_record("今天写了 WisePet 的规划", BASE))
    repo.append(_record("wisepet 打包有点慢", BASE + 100))
    repo.append(_record("无关的晚饭", BASE + 200))

    hits = repo.search("wisepet")

    assert [r.content for r in hits] == ["wisepet 打包有点慢",
                                         "今天写了 WisePet 的规划"]
    assert repo.search("") == []
    assert len(repo.search("wisepet", limit=1)) == 1


def should_let_chat_history_delegate_search_to_store(tmp_path):
    repo = SqliteChatRepository(tmp_path)
    repo.append(_record("WisePet 规划", BASE))
    history = ChatHistory(repo)

    assert [r.content for r in history.search("wisepet")] == ["WisePet 规划"]


# ---------- 旧数据迁移 ----------

def should_import_legacy_json_once_and_archive_it(tmp_path):
    day = day_key(BASE)
    _legacy_file(tmp_path, day, ["第一条", "第二条"])
    config = tmp_path / "config.json"
    config.write_text('{"ai":{}}', encoding="utf-8")

    repo = SqliteChatRepository(tmp_path)

    assert [r.content for r in repo.load(day)] == ["第一条", "第二条"]
    archived = list((tmp_path / "chat" / "legacy").glob("*.json.migrated"))
    assert len(archived) == 1
    assert list((tmp_path / "chat").glob("*.json")) == []
    assert json.loads(config.read_text(encoding="utf-8")) == {"ai": {}}


def should_not_duplicate_records_when_repo_is_reopened(tmp_path):
    day = day_key(BASE)
    _legacy_file(tmp_path, day, ["第一条", "第二条"])
    SqliteChatRepository(tmp_path)

    reopened = SqliteChatRepository(tmp_path)

    assert len(reopened.load(day)) == 2


def should_skip_corrupted_legacy_file_without_crashing(tmp_path):
    path = tmp_path / "chat" / "broken.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    repo = SqliteChatRepository(tmp_path)

    assert repo.days() == []
    assert len(list(path.parent.glob("broken.json.bak-*"))) == 1


# ---------- 损坏自愈 ----------

def should_rebuild_when_db_file_is_corrupted(tmp_path):
    db = tmp_path / CHAT_DB_NAME
    db.write_bytes(b"not a sqlite database")

    repo = SqliteChatRepository(tmp_path)
    repo.append(_record("重建之后", BASE))

    assert [r.content for r in repo.load(day_key(BASE))] == ["重建之后"]
    assert len(list(tmp_path.glob(f"{CHAT_DB_NAME}.bak-*"))) == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM chat_turns").fetchone()[0] == 1
