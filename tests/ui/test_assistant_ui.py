"""轴② A 类 UI/组装根冒烟：托付快速通道 / 资料注入 / 时间线删除 / 命令注册。"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import main
from fakes import FakeClock
from WisePet.domain.config_model import AppConfig
from WisePet.domain.product import PUSH_TITLES
from WisePet.infra.config_repo import JsonConfigRepository
from WisePet.infra.pet_state_repo import PetStateRepository
from WisePet.ui.memory_dialog import _ID_ROLE
from WisePet.ui.settings_dialog import SettingsDialog

_ASSETS = Path(__file__).resolve().parents[2] / "assets"


def _build(qapp, tmp_path):
    return main.PetApplication(
        qapp, config_repo=JsonConfigRepository(tmp_path),
        state_repo=PetStateRepository(tmp_path), assets_dir=_ASSETS,
        clock=FakeClock(), tray_available=True)


def _cfg(**assistant_changes):
    base = AppConfig()
    return replace(base, assistant=replace(base.assistant, **assistant_changes))


# ---------- 设置窗不丢新分节 ----------

def should_roundtrip_assistant_settings_in_dialog(qtbot):
    cfg = _cfg(enabled=False, knowledge_enabled=True,
               knowledge_dir="D:/notes", knowledge_top_k=5,
               event_daily_limit=1)
    dlg = SettingsDialog(cfg, skin_options=["cat"])
    qtbot.addWidget(dlg)
    assert dlg._knowledge_dir.text() == "D:/notes"
    assert dlg._assistant_enabled.isChecked() is False

    dlg._knowledge_dir.setText("E:/docs")
    dlg._event_limit.setValue(3)
    result, error = dlg.collect()
    assert error == ""
    assert result.assistant.knowledge_dir == "E:/docs"
    assert result.assistant.event_daily_limit == 3
    assert result.assistant.knowledge_top_k == 5        # 未动的字段保持
    assert result.companion.enabled is True             # 其它分节不丢


def should_keep_sections(qtbot):
    from PySide6.QtWidgets import QFrame
    dlg = SettingsDialog(AppConfig(), skin_options=["cat"])
    qtbot.addWidget(dlg)
    assert len(dlg.findChildren(QFrame, "section")) == 8   # 含「数据存储位置」


# ---------- 托付快速通道与完成信号 ----------

def should_create_task_from_chat_without_ai(qapp, tmp_path):
    pet_app = _build(qapp, tmp_path)
    try:
        pet_app.send_message("盯着 D:/a.log，出现 ERROR 就告诉我")
        tasks = pet_app._tasks.tasks()
        assert len(tasks) == 1 and tasks[0].kind == "watch_file"
        assert pet_app._config.assistant.tasks_enabled is True
    finally:
        pet_app.shutdown()


def should_resolve_task_and_recall_on_done_signal(qapp, tmp_path):
    pet_app = _build(qapp, tmp_path)
    try:
        pet_app.send_message("每隔30分钟提醒我交周报")
        assert len(pet_app._tasks.tasks()) == 1
        pet_app.send_message("周报搞定了")
        assert pet_app._tasks.tasks() == []
    finally:
        pet_app.shutdown()


# ---------- 本地资料索引与提示词注入 ----------

def should_rebuild_index_and_inject_into_prompt(qapp, tmp_path):
    folder = tmp_path / "notes"
    folder.mkdir()
    (folder / "plan.md").write_text("周报计划：周一交周报，周三复盘部署",
                                     encoding="utf-8")
    pet_app = _build(qapp, tmp_path)
    try:
        pet_app._config = _cfg(knowledge_enabled=True,
                               knowledge_dir=str(folder))
        pet_app._rebuild_knowledge(silent=True)
        assert pet_app._knowledge.status()["chunks"] >= 1
        pet_app._pending_query = "周报什么时候交"
        assert "以下是你读过的本机资料片段" in pet_app._system_prompt()
    finally:
        pet_app.shutdown()


def should_not_inject_knowledge_when_disabled(qapp, tmp_path):
    pet_app = _build(qapp, tmp_path)
    try:
        pet_app._pending_query = "周报"
        assert "以下是你读过的本机资料片段" not in pet_app._system_prompt()
    finally:
        pet_app.shutdown()


# ---------- 可遗忘时间线 ----------

def should_really_forget_a_memory_fact_from_dialog(qapp, tmp_path):
    pet_app = _build(qapp, tmp_path)
    try:
        pet_app._memory_store.save(
            {"user_name": "小明", "activity": "", "project": ""}, [])
        dlg = pet_app._memory_dialog
        dlg.reload()
        target = next(i for i in range(dlg._list.count())
                      if dlg._list.item(i).data(_ID_ROLE) == "memory:user_name")
        dlg._list.item(target).setSelected(True)
        assert dlg.selected_ids() == ["memory:user_name"]
        dlg._delete_selected()
        assert pet_app._memory_store.load()["user_name"] == ""
    finally:
        pet_app.shutdown()


# ---------- 命令与预算白名单 ----------

def should_register_assistant_commands():
    ids = [entry[0] for entry in main.COMMANDS]
    assert {"tasks", "cancel_tasks", "knowledge_build",
            "knowledge_status"} <= set(ids)


def should_budget_new_push_titles():
    assert {"接下托付", "我盯着的事有动静了", "还惦记着呢",
            "你回来啦", "夜深了", "今天很拼"} <= set(PUSH_TITLES)


def should_count_new_services_in_main_tick(qapp, tmp_path, monkeypatch):
    pet_app = _build(qapp, tmp_path)
    try:
        called = []
        monkeypatch.setattr(pet_app._tasks, "tick",
                            lambda now: called.append("tasks"))
        monkeypatch.setattr(pet_app._events, "tick",
                            lambda now: called.append("events"))
        pet_app.tick()
        assert called == ["tasks", "events"]
    finally:
        pet_app.shutdown()
