"""本地数据管家：可导出、可删除、可看占用（隐私承诺的另一半）。"""
from __future__ import annotations

from WisePet.app.data_steward import DataSteward, render_records
from WisePet.domain.chat_record import ChatRecord, day_key
from WisePet.infra.sqlite_chat_repo import SqliteChatRepository

BASE = 1_700_000_000.0


def _record(content, ts, role="user"):
    return ChatRecord(role=role, content=content, source="local",
                      ts=ts, session_id=day_key(ts))


def _filled(tmp_path):
    repo = SqliteChatRepository(tmp_path)
    repo.append(_record("今天怎么样", BASE))
    repo.append(_record("在呢，摸摸头", BASE + 30, role="assistant"))
    return DataSteward(repo, tmp_path), repo


def should_render_markdown_with_roles_and_content():
    text = render_records([_record("你好", BASE)], day_key(BASE), "md")
    assert "你" in text and "你好" in text and day_key(BASE) in text


def should_render_json_as_a_list_of_records():
    import json
    data = json.loads(render_records([_record("你好", BASE)], day_key(BASE), "json"))
    assert data[0]["content"] == "你好" and data[0]["role"] == "user"


def should_export_all_days_and_report_record_count(tmp_path):
    steward, _repo = _filled(tmp_path)
    target = tmp_path / "export.md"

    assert steward.export_all(target) == 2
    assert "今天怎么样" in target.read_text(encoding="utf-8")
    assert "在呢，摸摸头" in target.read_text(encoding="utf-8")


def should_export_as_json_when_requested(tmp_path):
    import json
    steward, _repo = _filled(tmp_path)
    target = tmp_path / "export.json"

    assert steward.export_all(target, fmt="json") == 2
    assert len(json.loads(target.read_text(encoding="utf-8"))) == 2


def should_delete_one_day_without_touching_others(tmp_path):
    steward, repo = _filled(tmp_path)
    other_day = day_key(BASE + 86400 * 3)
    repo.append(_record("另一天", BASE + 86400 * 3))

    assert steward.delete_day(day_key(BASE)) == 2
    assert repo.load(day_key(BASE)) == []
    assert len(repo.load(other_day)) == 1


def should_report_local_disk_usage_without_reading_message_content(tmp_path):
    steward, _repo = _filled(tmp_path)
    (tmp_path / "logs").mkdir(exist_ok=True)
    (tmp_path / "logs" / "WisePet.log").write_text("x" * 200, encoding="utf-8")

    usage = steward.usage()

    assert usage["records"] == 2 and usage["days"] == 1
    assert usage["db_kb"] > 0 and usage["logs_kb"] > 0


def should_clear_telemetry_when_user_asks(tmp_path):
    steward, _repo = _filled(tmp_path)

    class _FakeTelemetry:
        cleared = False

        def clear(self):
            self.cleared = True

    fake = _FakeTelemetry()
    steward._telemetry = fake
    steward.clear_telemetry()
    assert fake.cleared is True
