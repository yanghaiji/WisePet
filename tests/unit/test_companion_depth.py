"""情感陪伴五件事的纯逻辑回归。"""
from __future__ import annotations

from WisePet.domain.companion_depth import (
    discoverability_line, memory_visibility_line, mood_tone_line,
    name_first_line, schedule_empathy_line, sulk_line,
)
from WisePet.domain.pet import Pet
from WisePet.domain.position import PositionVO
from WisePet.domain.task_protocol import (
    WATCH_PAGE, is_safe_watch_url, page_changed, parse_task_command,
)
from WisePet.domain.vitals import VitalsVO


# ---------- 第 1 件：记忆可见化 ----------

def test_memory_visibility_and_name_first():
    assert memory_visibility_line("") == ""
    hint = memory_visibility_line("用户名叫小明")
    assert "自然" in hint and "记得" in hint and "不要" in hint
    assert name_first_line(False, True) == ""
    assert name_first_line(True, False) == ""
    assert "名字" in name_first_line(True, True)


# ---------- 第 3 件：情绪语气 + 作息 ----------

def test_mood_tone_by_vitals():
    assert "低落" in mood_tone_line(10, 50)
    assert "疲惫" in mood_tone_line(50, 10)
    assert "活泼" in mood_tone_line(90, 90)
    assert mood_tone_line(50, 50) == ""


def test_schedule_empathy_and_sulk():
    assert "深夜" in schedule_empathy_line(23, 2)
    assert "想念" in schedule_empathy_line(10, 2, idle_hours=30)
    assert "打气" in schedule_empathy_line(8, 0)
    assert schedule_empathy_line(14, 2) == ""
    assert "不理" in sulk_line(48, 30)
    assert sulk_line(48, 80) == ""


def test_pet_exposes_readonly_vitals():
    pet = Pet("p1", "小墨", vitals=VitalsVO(50, 30, 40), position=PositionVO(0, 0))
    assert pet.vitals.mood == 30
    before = pet.vitals.mood
    # 只读视图不可写回对象本身（VitalsVO 是 frozen）
    assert before == 30


# ---------- 第 5 件：可发现性 ----------

def test_discoverability_line_matches_intent():
    assert "休息" in discoverability_line("我今天好累啊")
    assert "记" in discoverability_line("我怕忘了交周报")
    assert "查" in discoverability_line("今天天气怎么样")
    assert "周边" in discoverability_line("附近有什么咖啡")
    assert discoverability_line("你好") == ""
    assert discoverability_line("") == ""


# ---------- 第 4 件：页面监控 ----------

def test_parse_watch_page_command():
    spec = parse_task_command(
        "盯着 https://www.example.com/track，出现 已发货 就告诉我", now=100.0)
    assert spec is not None and spec.kind == WATCH_PAGE
    assert spec.target.startswith("https://")
    assert spec.match == "已发货" and spec.meta["watch"] == "keyword"

    change = parse_task_command("帮我盯 https://example.com/a，有变化告诉我")
    assert change is not None and change.kind == WATCH_PAGE
    assert change.meta["watch"] == "change" and change.match == ""


def test_safe_watch_url_blocks_ssrf():
    assert is_safe_watch_url("https://example.com/a") is True
    assert is_safe_watch_url("http://localhost:8080") is False
    assert is_safe_watch_url("http://127.0.0.1") is False
    assert is_safe_watch_url("http://192.168.1.1") is False
    assert is_safe_watch_url("ftp://example.com") is False
    assert is_safe_watch_url("") is False


def test_page_changed_logic():
    assert page_changed("", "some content") is False       # 首抓记基线
    assert page_changed("old", "new") is True
    assert page_changed("same", "same") is False
    assert page_changed("no keyword", "有 已发货 啦", "已发货") is True
    assert page_changed("已发货", "还是已发货", "已发货") is True  # 关键词在就提示
