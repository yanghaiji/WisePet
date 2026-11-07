"""P1/P2 UI 与组装根冒烟：引导 / 场景包 / 免打扰 / 检索 / 预览确认 / 撤销。"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import main
from PySide6.QtWidgets import QLineEdit
from fakes import FakeClock
from WisePet.app.action_protocol import CreateFileAction
from WisePet.domain.config_model import AppConfig
from WisePet.domain.events import SpeakRequestedEvent
from WisePet.infra.config_repo import JsonConfigRepository
from WisePet.infra.pet_state_repo import PetStateRepository
from WisePet.ui.action_confirm import ActionConfirmDialog
from WisePet.ui.chat_window import ChatWindow
from WisePet.ui.onboarding_dialog import OnboardingDialog
from WisePet.ui.settings_dialog import SettingsDialog

_ASSETS = Path(__file__).resolve().parents[2] / "assets"


def _build(qapp, tmp_path, clock=None):
    return main.PetApplication(
        qapp, config_repo=JsonConfigRepository(tmp_path),
        state_repo=PetStateRepository(tmp_path), assets_dir=_ASSETS,
        clock=clock or FakeClock(), tray_available=True)


def _record(role: str, content: str, ts: float = 1.0):
    from WisePet.domain.chat_record import ChatRecord
    return ChatRecord(role=role, content=content, source="local", ts=ts)


# ---------- 首次价值时刻 ----------

def should_walk_through_three_onboarding_steps(qtbot):
    dialog = OnboardingDialog()
    qtbot.addWidget(dialog)
    seen: list[str] = []
    dialog.try_chat.connect(lambda: seen.append("chat"))
    dialog.try_reminder.connect(lambda: seen.append("reminder"))
    dialog.completed.connect(lambda: seen.append("done"))

    assert dialog.current_step == 0
    for _ in range(3):
        dialog._next.click()

    assert seen == ["chat", "reminder", "done"]
    assert dialog.isVisible() is False


def should_let_user_skip_onboarding(qtbot):
    dialog = OnboardingDialog()
    qtbot.addWidget(dialog)
    done: list[bool] = []
    dialog.completed.connect(lambda: done.append(True))
    dialog._skip.click()
    assert done == [True]


# ---------- 跨日检索 ----------

def should_render_search_results_and_exit_search_mode(qtbot, fake_screen):
    results = [_record("user", "WisePet 规划"), _record("assistant", "收到")]
    queries: list[str] = []
    window = ChatWindow(fake_screen,
                        days_provider=lambda: ["2026-11-06"],
                        records_provider=lambda _day: [_record("assistant", "当天记录")],
                        search_provider=lambda q: (queries.append(q), results)[1])
    qtbot.addWidget(window)

    assert isinstance(window.search_input, QLineEdit)
    assert window.search("wisepet") == results
    assert window.searching is True and "搜索" in window.status_text
    assert queries == ["wisepet"]

    window.search("")
    assert window.searching is False and "当天记录" in window._history.toPlainText()


# ---------- 预览确认 ----------

def should_expose_permission_decision_as_pure_policy(qtbot):
    assert ActionConfirmDialog.decide(False, True) == "deny_once"
    assert ActionConfirmDialog.decide(True, True) == "auto"
    assert ActionConfirmDialog.decide(True, False) == "confirm"

    dialog = ActionConfirmDialog("创建文件：D:/a.md", "目标：D:/a.md\n内容：hello")
    qtbot.addWidget(dialog)
    dialog.set_remember_enabled(False)
    assert dialog._remember.isEnabled() is False


# ---------- 设置窗保留新字段 ----------

def should_keep_product_settings_when_saving_dialog(qtbot):
    base = replace(AppConfig(),
                   quiet=replace(AppConfig().quiet, daily_limit=7, muted=True),
                   behavior=replace(AppConfig().behavior, context_driven=False),
                   memory=replace(AppConfig().memory, enabled=False,
                                  search_max_results=88),
                   actions=replace(AppConfig().actions, write_permission="auto"))
    dialog = SettingsDialog(base)
    qtbot.addWidget(dialog)
    cfg, error = dialog.collect()

    assert error == ""
    assert cfg.quiet.daily_limit == 7 and cfg.quiet.muted is True
    assert cfg.behavior.context_driven is False
    assert cfg.memory.enabled is False and cfg.memory.search_max_results == 88
    assert cfg.actions.write_permission == "auto"


# ---------- 组装根接线 ----------

def should_wire_product_services_and_scene_presets(qtbot, qapp, tmp_path):
    pet_app = _build(qapp, tmp_path)
    try:
        assert pet_app._quiet_budget is not None
        assert pet_app._memory is not None
        assert pet_app._onboarding_dialog is not None
        assert len(pet_app._menu.scene_actions) == 3
        assert pet_app._menu.act_undo.isEnabled() is False

        key_before = pet_app._config.ai.api_key
        pet_app._apply_scene("deep_work")
        assert pet_app._config.news.enabled is False
        assert pet_app._config.quiet.daily_limit == 6
        assert pet_app._config.ai.api_key == key_before
    finally:
        pet_app.shutdown()


def should_toggle_mute_and_reflect_it_in_menu(qtbot, qapp, tmp_path):
    pet_app = _build(qapp, tmp_path)
    try:
        assert pet_app._config.quiet.muted is False
        pet_app._toggle_mute()
        assert pet_app._config.quiet.muted is True
        assert "开" in pet_app._menu.act_mute.text()
        pet_app._toggle_mute()
        assert pet_app._config.quiet.muted is False
    finally:
        pet_app.shutdown()


def should_let_user_initiated_speech_through_while_muted(qtbot, qapp, tmp_path,
                                                         monkeypatch):
    pet_app = _build(qapp, tmp_path)
    try:
        pet_app.apply_config(replace(
            pet_app._config, quiet=replace(pet_app._config.quiet, muted=True)))
        shown: list[str] = []
        monkeypatch.setattr(pet_app._bubble, "show_text",
                            lambda *args, **kwargs: shown.append(args[0]))
        monkeypatch.setattr(pet_app._tts, "announce", lambda *args, **kwargs: None)

        pet_app._on_speak(SpeakRequestedEvent("主动推送", source="cheer"))
        pet_app._on_speak(SpeakRequestedEvent("用户的回复", source="chat"))

        assert shown == ["用户的回复"]
    finally:
        pet_app.shutdown()


def should_reconfigure_logging_only_when_system_settings_changed(qtbot, qapp,
                                                                 tmp_path,
                                                                 monkeypatch):
    pet_app = _build(qapp, tmp_path)
    try:
        calls: list = []
        monkeypatch.setattr(
            main, "reconfigure_logging",
            lambda system, data_dir=None: calls.append(system))

        updated = replace(pet_app._config,
                          system=replace(pet_app._config.system, log_max_mb=20))
        pet_app.apply_config(updated)
        assert len(calls) == 1 and calls[0].log_max_mb == 20

        pet_app.apply_config(replace(updated, pet=replace(
            updated.pet, walk_speed_px_per_s=70.0)))
        assert len(calls) == 1              # 系统配置没变，不重建日志 handler
    finally:
        pet_app.shutdown()


def should_explain_what_a_scene_actually_changed(qtbot, qapp, tmp_path,
                                                  monkeypatch):
    """用户点了「深度工作」却不知道它关掉了所有主动推送，必须当场说清。"""
    pet_app = _build(qapp, tmp_path)
    try:
        shown: list[str] = []
        monkeypatch.setattr(pet_app._bubble, "show_text",
                            lambda text, **kw: shown.append(text))
        monkeypatch.setattr(pet_app._tts, "announce", lambda *a, **kw: None)

        pet_app._apply_scene("deep_work")

        assert any("新闻" in t and "鼓励" in t for t in shown)
    finally:
        pet_app.shutdown()


def should_tell_user_once_when_push_quota_is_exhausted(qtbot, qapp, tmp_path,
                                                       monkeypatch):
    """额度满了不能无声静默：当天给一条解释，且只给一次。"""
    from WisePet.domain.events import SpeakRequestedEvent

    pet_app = _build(qapp, tmp_path)
    try:
        pet_app.apply_config(replace(
            pet_app._config,
            quiet=replace(pet_app._config.quiet, daily_limit=1)))
        shown: list[str] = []
        monkeypatch.setattr(pet_app._bubble, "show_text",
                            lambda text, **kw: shown.append(text))
        monkeypatch.setattr(pet_app._tts, "announce", lambda *a, **kw: None)

        pet_app._on_speak(SpeakRequestedEvent("第一条", source="cheer"))
        pet_app._on_speak(SpeakRequestedEvent("第二条", source="news"))

        assert sum("额度" in t for t in shown) == 1
    finally:
        pet_app.shutdown()


def should_undo_last_written_file(qtbot, qapp, tmp_path, monkeypatch):
    pet_app = _build(qapp, tmp_path)
    try:
        monkeypatch.setattr(pet_app._tts, "announce", lambda *args, **kwargs: None)
        target = tmp_path / "notes.txt"
        target.write_text("旧内容", encoding="utf-8")
        action = CreateFileAction(path=str(target), content="新内容")

        pet_app._last_action_receipt = pet_app._snapshot_before_write(action)
        pet_app._menu.set_undo_enabled(True)
        pet_app._write_file(action)
        pet_app._undo_last_action()

        assert target.read_text(encoding="utf-8") == "旧内容"
        assert pet_app._last_action_receipt is None
        assert pet_app._menu.act_undo.isEnabled() is False
    finally:
        pet_app.shutdown()
