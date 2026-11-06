"""按天聊天记录仓储：一天一文件、只追加不截断、损坏自动备份（FR v1.8 C13）。"""
from __future__ import annotations

import json

from WisePet.domain.chat_record import ChatRecord, day_key
from WisePet.infra.chat_log_repo import CHAT_DIR_NAME, ChatLogRepository


def _record(content, ts, role="user", source="user"):
    return ChatRecord(role=role, content=content, source=source,
                      ts=ts, session_id=day_key(ts))


def should_append_records_into_same_day_file(tmp_path):
    repo = ChatLogRepository(tmp_path)
    base = 1_700_000_000.0
    repo.append(_record("你好", base))
    repo.append(_record("在呢", base + 60, role="assistant", source="local"))

    loaded = repo.load(day_key(base))
    assert [r.content for r in loaded] == ["你好", "在呢"]
    assert [r.source for r in loaded] == ["user", "local"]
    day_file = tmp_path / CHAT_DIR_NAME / f"{day_key(base)}.json"
    data = json.loads(day_file.read_text(encoding="utf-8"))
    assert data["date"] == day_key(base) and len(data["records"]) == 2


def should_separate_files_by_day_without_truncating(tmp_path):
    repo = ChatLogRepository(tmp_path)
    base = 1_700_000_000.0
    for i in range(25):
        repo.append(_record(f"第{i}条", base + i * 60))
    repo.append(_record("第二天", base + 86400 * 3))

    assert len(repo.days()) == 2
    assert len(repo.load(day_key(base))) == 25          # 不像 ChatSession 那样截断
    assert len(repo.load(day_key(base + 86400 * 3))) == 1


def should_return_empty_when_no_file_for_day(tmp_path):
    assert ChatLogRepository(tmp_path).load("2026-11-05") == []
    assert ChatLogRepository(tmp_path).days() == []


def should_backup_corrupted_file_and_return_empty(tmp_path):
    repo = ChatLogRepository(tmp_path)
    day = day_key(1_700_000_000.0)
    path = repo.path_for(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    assert repo.load(day) == []
    assert len(list(path.parent.glob(f"{day}.json.bak-*"))) == 1


def should_keep_full_unicode_content_when_saved(tmp_path):
    repo = ChatLogRepository(tmp_path)
    base = 1_700_000_000.0
    repo.append(_record("今天心情不太好…<摸摸头>", base))
    assert repo.load(day_key(base))[0].content == "今天心情不太好…<摸摸头>"
