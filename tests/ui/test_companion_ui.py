"""陪伴轴① UI 冒烟：设置窗不丢关系感配置 / 叫一声改状态 / 久别问候 / 情境提示词。"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import main
from fakes import FakeClock
from WisePet.domain.config_model import AppConfig
from WisePet.domain.state_machine import BehaviorState
from WisePet.infra.config_repo import JsonConfigRepository
from WisePet.infra.pet_state_repo import PetStateRepository
from WisePet.ui.settings_dialog import SettingsDialog

_ASSETS = Path(__file__).resolve().parents[2] / "assets"


def _build(qapp, tmp_path, clock=None):
    return main.PetApplication(
        qapp, config_repo=JsonConfigRepository(tmp_path),
        state_repo=PetStateRepository(tmp_path), assets_dir=_ASSETS,
        clock=clock or FakeClock(), tray_available=True)


def should_keep_companion_settings_when_dialog_saved(qtbot):
    cfg = AppConfig()
    dlg = SettingsDialog(cfg, skin_options=["cat"])
    qtbot.addWidget(dlg)
    dlg._companion_enabled.setChecked(False)
    dlg._recall_enabled.setChecked(False)
    dlg._companion_interactive.setChecked(False)
    dlg._reunion_hours.setValue(8)

    result, error = dlg.collect()
    assert error == ""
    assert result.companion.enabled is False
    assert result.companion.recall_enabled is False
    assert result.companion.interactive is False
    assert result.companion.reunion_min_hours == 8.0
    assert result.companion.contextual is True       # 未动的字段保持原值


def should_reload_companion_values_into_dialog(qtbot):
    cfg = replace(AppConfig(), companion=replace(
        AppConfig().companion, enabled=False, reunion_min_hours=6.0,
        contextual=False))
    dlg = SettingsDialog(cfg, skin_options=["cat"])
    qtbot.addWidget(dlg)

    assert dlg._companion_enabled.isChecked() is False
    assert dlg._companion_contextual.isChecked() is False
    assert dlg._reunion_hours.value() == 6


def should_turn_to_user_when_called(qapp, tmp_path):
    pet_app = _build(qapp, tmp_path)
    try:
        assert pet_app._pet.state is BehaviorState.IDLE
        pet_app._call_pet()
        assert pet_app._pet.state is BehaviorState.SPEAKING
    finally:
        pet_app.shutdown()


def should_use_reunion_text_for_startup_greeting(qapp, tmp_path):
    pet_app = _build(qapp, tmp_path)
    try:
        # 直接给服务灌一个「离开 80 小时」的事实，避开线程与启动时序
        pet_app._companion._gap_hours = 80.0
        assert "好几天没见" in pet_app._greeting_text()
    finally:
        pet_app.shutdown()


def should_inject_context_and_recall_into_system_prompt(qapp, tmp_path):
    pet_app = _build(qapp, tmp_path)
    try:
        prompt = pet_app._system_prompt()
        assert "当前时间" in prompt
        # 注入一条惦记，系统提示词里应当出现「惦记」
        from WisePet.domain.companion import RecallCandidate
        pet_app._companion._candidate = RecallCandidate(
            text="明天打算把周报写完", ts=pet_app._clock.now() - 3 * 86_400,
            days_ago=3)
        pet_app._companion._candidate_loaded = True
        assert "惦记" in pet_app._system_prompt()
    finally:
        pet_app.shutdown()


def should_register_companion_commands():
    ids = [entry[0] for entry in main.COMMANDS]
    assert "call" in ids and "recall" in ids
