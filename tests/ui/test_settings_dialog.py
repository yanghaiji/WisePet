"""设置窗冒烟：api_key 永不回显、留空即不修改、区间校验、测试连接（PRD M7 / US-6）。"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (QApplication, QAbstractSpinBox, QDialog, QDialogButtonBox,
                               QFrame, QLineEdit, QToolButton)
from conftest import mouse_event

from WisePet.app.chat_service import ChatService
from WisePet.domain.config_model import AppConfig
from WisePet.domain.errors import AIGatewayError
from WisePet.domain.ports import AIConfig
from WisePet.infra.config_repo import JsonConfigRepository, mask_key
from WisePet.ui import settings_dialog as settings_module
from WisePet.ui.settings_dialog import SettingsDialog

_SECRET = "sk-abcdef1234567890xy"


@pytest.fixture
def base_config():
    return AppConfig(ai=AIConfig(api_key=_SECRET, model="m1", temperature=0.5, timeout_s=12.0))


def _click(dialog, qtbot, role) -> None:
    box = dialog.findChild(QDialogButtonBox)
    qtbot.mouseClick(box.button(role), Qt.LeftButton)


def should_keep_key_hidden_when_dialog_opened(qtbot, base_config):
    dialog = SettingsDialog(base_config)
    qtbot.addWidget(dialog)

    assert dialog._api_key.text() == ""
    assert dialog._api_key.echoMode() == QLineEdit.Password
    assert mask_key(_SECRET) in dialog._api_key.placeholderText()
    assert _SECRET not in dialog._api_key.placeholderText()


def should_hint_local_mode_when_no_key_stored(qtbot):
    """未配 Key 不是功能缺失：占位文案需告知本地小脑可用（PRD v1.1 C2）。"""
    dialog = SettingsDialog(AppConfig(ai=AIConfig(api_key="")))
    qtbot.addWidget(dialog)

    hint = dialog._api_key.placeholderText()

    assert "本地小脑" in hint
    assert "不修改" not in hint


def should_load_current_values_when_dialog_opened(qtbot, base_config):
    dialog = SettingsDialog(base_config)
    qtbot.addWidget(dialog)

    assert dialog._model.text() == "m1"
    assert dialog._timeout.value() == 12
    assert dialog._water_interval.value() == 45
    assert dialog._water_enabled.isChecked()


def should_keep_stored_key_when_field_left_blank(qtbot, base_config):
    dialog = SettingsDialog(base_config)
    qtbot.addWidget(dialog)

    cfg, error = dialog.collect()

    assert error == ""
    assert cfg.ai.api_key == _SECRET


def should_take_typed_key_when_user_edits_field(qtbot, base_config):
    dialog = SettingsDialog(base_config)
    qtbot.addWidget(dialog)
    new_key = "sk-new-key-000111222333"
    dialog._api_key.setText(new_key)

    cfg, _error = dialog.collect()

    assert cfg.ai.api_key == new_key


def should_fall_back_to_originals_when_text_fields_cleared(qtbot, base_config):
    """误清空不能把配置写成空串（PRD M7 边界）。"""
    dialog = SettingsDialog(base_config)
    qtbot.addWidget(dialog)
    dialog._base_url.clear()
    dialog._model.clear()
    dialog._persona_name.clear()
    dialog._system_prompt.clear()

    cfg, _error = dialog.collect()

    assert cfg.ai.base_url == base_config.ai.base_url
    assert cfg.ai.model == "m1"
    assert cfg.persona.name == base_config.persona.name
    assert cfg.persona.system_prompt == base_config.persona.system_prompt


def should_collect_all_form_values_when_user_edits(qtbot, base_config):
    dialog = SettingsDialog(base_config)
    qtbot.addWidget(dialog)
    dialog._walk_speed.setValue(120.0)
    dialog._max_turns.setValue(8)
    dialog._temperature.setValue(1.2)
    dialog._sedentary_enabled.setChecked(False)
    dialog._water_interval.setValue(30)

    cfg, _error = dialog.collect()

    assert cfg.pet.walk_speed_px_per_s == 120.0
    assert cfg.pet.max_turns == 8
    assert cfg.pet.click_move_threshold_px == base_config.pet.click_move_threshold_px
    assert cfg.ai.temperature == pytest.approx(1.2)
    assert cfg.reminders.sedentary.enabled is False
    assert cfg.reminders.water.interval_min == 30.0


def should_let_pet_stay_anywhere_when_stick_to_floor_off(qtbot, base_config):
    """关掉贴地才能拖到任意位置；保存设置也不该把 v2.0 位移参数重置掉。"""
    base_config.pet.run_speed_px_per_s = 222.0
    dialog = SettingsDialog(base_config)
    qtbot.addWidget(dialog)

    assert dialog._stick_to_floor.isChecked()
    dialog._stick_to_floor.setChecked(False)

    cfg, _error = dialog.collect()

    assert cfg.pet.stick_to_floor is False
    assert cfg.pet.run_speed_px_per_s == 222.0
    assert cfg.pet.fall_speed_px_per_s == base_config.pet.fall_speed_px_per_s


def should_roundtrip_chat_idle_hide_seconds(qtbot, base_config):
    """聊天框空闲隐藏时长：默认回填 30，改后保存能取回（PRD 需求）。"""
    dialog = SettingsDialog(base_config)
    qtbot.addWidget(dialog)

    assert dialog._chat_idle_hide.value() == 30
    dialog._chat_idle_hide.setValue(15)

    cfg, error = dialog.collect()

    assert error == ""
    assert cfg.chat.idle_hide_s == 15.0


def should_roundtrip_companion_features_when_edited(qtbot, base_config):
    """v2.2 四项（语音/系统通知/周报/成长）：默认回填 + 改后 collect 取回。"""
    dialog = SettingsDialog(base_config)
    qtbot.addWidget(dialog)

    # 默认值回填
    assert dialog._tts_enabled.isChecked() is False
    assert dialog._tts_volume.value() == 100
    assert dialog._notify_system.isChecked() is False
    assert dialog._weekly_enabled.isChecked() is True
    assert dialog._weekly_weekday.currentData() == 6
    assert dialog._growth_enabled.isChecked() is True

    dialog._tts_enabled.setChecked(True)
    dialog._tts_volume.setValue(60)
    dialog._tts_rate.setValue(-3)
    dialog._notify_system.setChecked(True)
    dialog._weekly_weekday.setCurrentIndex(0)   # 周一
    dialog._weekly_hour.setValue(9)
    dialog._growth_enabled.setChecked(False)

    cfg, error = dialog.collect()

    assert error == ""
    assert cfg.tts.enabled is True and cfg.tts.volume == 60 and cfg.tts.rate == -3
    assert cfg.notification.use_system is True
    assert cfg.weekly.enabled is True and cfg.weekly.weekday == 0 and cfg.weekly.hour == 9
    assert cfg.growth.enabled is False
    # 未动字段不被重置
    assert cfg.bubble.duration_s == base_config.bubble.duration_s


def should_reject_when_idle_min_exceeds_max(qtbot, base_config):
    dialog = SettingsDialog(base_config)
    qtbot.addWidget(dialog)
    dialog._idle_min.setValue(30)
    dialog._idle_max.setValue(10)

    cfg, error = dialog.collect()

    assert cfg is None
    assert "闲置间隔" in error


def should_enforce_interval_minimum_when_user_lowers_it(qtbot, base_config):
    """间隔下限由表单硬性约束，提交值永不越界（PRD M6 边界）。"""
    dialog = SettingsDialog(base_config)
    qtbot.addWidget(dialog)
    minimum = base_config.reminders.min_interval_min

    assert dialog._water_interval.minimum() == int(minimum)

    dialog._water_interval.setValue(1)
    cfg, error = dialog.collect()

    assert error == ""
    assert cfg.reminders.water.interval_min == minimum


def should_persist_key_and_mask_status_when_saved(qtbot, base_config, tmp_path):
    repo = JsonConfigRepository(tmp_path)
    dialog = SettingsDialog(repo.load(), on_saved=repo.save)
    qtbot.addWidget(dialog)
    typed = "sk-live-9988776655"
    dialog._api_key.setText(typed)

    _click(dialog, qtbot, QDialogButtonBox.Save)

    assert repo.load().ai.api_key == typed
    assert dialog.result() == QDialog.Accepted
    assert mask_key(typed) in dialog._status.text()
    assert typed not in dialog._status.text()


def should_adapt_local_mode_when_saving_without_key(qtbot, tmp_path):
    """空 Key 保存后告知进入了本地小脑，而非报错（PRD v1.1 AC-LOCAL-1）。"""
    repo = JsonConfigRepository(tmp_path)
    dialog = SettingsDialog(AppConfig(ai=AIConfig(api_key="")), on_saved=repo.save)
    qtbot.addWidget(dialog)

    _click(dialog, qtbot, QDialogButtonBox.Save)

    assert dialog.result() == QDialog.Accepted
    assert repo.load().ai.configured is False
    assert "本地小脑" in dialog._status.text()


def should_stay_unsaved_when_validation_blocks_save(qtbot, base_config, tmp_path):
    repo = JsonConfigRepository(tmp_path)
    dialog = SettingsDialog(repo.load(), on_saved=repo.save)
    qtbot.addWidget(dialog)
    dialog._idle_min.setValue(30)
    dialog._idle_max.setValue(10)

    _click(dialog, qtbot, QDialogButtonBox.Save)

    assert dialog.result() != QDialog.Accepted
    assert not repo.path.exists()


def should_not_mutate_given_config_when_user_edits_then_cancels(qtbot, base_config):
    dialog = SettingsDialog(base_config)
    qtbot.addWidget(dialog)
    dialog._water_interval.setValue(99)
    dialog._api_key.setText("sk-throwaway-1234")

    _click(dialog, qtbot, QDialogButtonBox.Cancel)

    assert base_config.ai.api_key == _SECRET
    assert base_config.reminders.water.interval_min == 45.0


def should_show_readable_reason_when_probe_fails(qtbot, base_config, monkeypatch):
    def _fail(_cfg):
        raise AIGatewayError(AIGatewayError.AUTH, "http 401")

    monkeypatch.setattr(settings_module, "probe", _fail)
    dialog = SettingsDialog(base_config)
    qtbot.addWidget(dialog)

    dialog._test_connection()

    assert ChatService.fallback_text(AIGatewayError.AUTH) in dialog._status.text()
    assert "http 401" in dialog._status.text()


def should_report_success_when_probe_passes(qtbot, base_config, monkeypatch):
    monkeypatch.setattr(settings_module, "probe", lambda _cfg: None)
    dialog = SettingsDialog(base_config)
    qtbot.addWidget(dialog)

    dialog._test_connection()

    assert "连接成功" in dialog._status.text()


class TestStickerStyle:
    """FR v1.12 C22：设置窗与聊天界面同一套贴纸风，不再是系统控件观感。"""

    def should_be_frameless_translucent_card_when_opened(self, qtbot, base_config):
        dialog = SettingsDialog(base_config)
        qtbot.addWidget(dialog)

        assert dialog.windowFlags() & Qt.FramelessWindowHint
        assert dialog.testAttribute(Qt.WA_TranslucentBackground)
        style = dialog.styleSheet()
        assert "settingsCard" in style
        assert "border-radius" in style
        assert "#FFFDF7" in style and "#DFC49A" in style
        assert dialog.findChild(QFrame, "settingsCard") is not None

    def should_close_from_custom_title_bar(self, qtbot, base_config):
        dialog = SettingsDialog(base_config)
        qtbot.addWidget(dialog)
        dialog.show()

        assert dialog.close_button.text() == "×"
        dialog.close_button.click()

        assert not dialog.isVisible()

    def should_keep_seven_sections_in_scrollable_card(self, qtbot, base_config):
        dialog = SettingsDialog(base_config)
        qtbot.addWidget(dialog)

        assert dialog._scroll.widgetResizable()
        # 5 个业务分区 + 「体验与隐私」+「系统配置」
        assert len(dialog.findChildren(QFrame, "section")) == 7

    def should_use_same_palette_for_buttons_and_status(self, qtbot, base_config):
        dialog = SettingsDialog(base_config)
        qtbot.addWidget(dialog)

        box = dialog.findChild(QDialogButtonBox)
        assert box.button(QDialogButtonBox.Save).objectName() == "saveBtn"
        assert dialog._test_btn.objectName() == "testBtn"

        dialog._show_status("连接成功 ✓", ok=True)
        assert "border-radius" in dialog._status.styleSheet()
        dialog._show_status("出错了", ok=False)
        assert "#F7E2E2" in dialog._status.styleSheet()

    def should_move_when_title_bar_dragged(self, qtbot, base_config):
        dialog = SettingsDialog(base_config)
        qtbot.addWidget(dialog)
        dialog.show()
        before = dialog.pos()

        dialog._begin_drag(QPoint(10, 10))
        dialog._move_by(QPoint(60, 30))

        assert dialog.pos() != before

    def should_move_when_title_bar_blank_area_dragged_by_mouse(self, qtbot, base_config):
        """FR v1.13 C23：整条标题栏都能拖，不能只在圆点/文字上生效。"""
        dialog = SettingsDialog(base_config)
        qtbot.addWidget(dialog)
        dialog.show()
        before = dialog.pos()
        bar = dialog.title_bar

        QApplication.sendEvent(bar, mouse_event(QEvent.MouseButtonPress,
                                                (20, 10), (120, 110)))
        QApplication.sendEvent(bar, mouse_event(QEvent.MouseMove, (80, 40),
                                                (180, 140), button=Qt.NoButton,
                                                buttons=Qt.LeftButton))
        QApplication.sendEvent(bar, mouse_event(QEvent.MouseButtonRelease,
                                                (80, 40), (180, 140),
                                                button=Qt.LeftButton,
                                                buttons=Qt.NoButton))

        assert dialog.pos() != before

    def should_hide_empty_status_bar_until_message(self, qtbot, base_config):
        """空状态不该占一条米色横条；有结果时才出现。"""
        dialog = SettingsDialog(base_config)
        qtbot.addWidget(dialog)

        assert dialog._status.isHidden()

        dialog._show_status("连接成功 ✓", ok=True)

        assert not dialog._status.isHidden()
        assert dialog._status.text() == "连接成功 ✓"

    def should_replace_native_spinbox_arrows_with_sticker_buttons(self, qtbot, base_config):
        """原生箭头最终换成自绘图标按钮（QSS border 三角形会渲染成实心方块）。"""
        dialog = SettingsDialog(base_config)
        qtbot.addWidget(dialog)
        style = dialog.styleSheet()

        assert "QToolButton#stepUp" in style
        assert "QToolButton#stepDown" in style
        assert "padding-right" in style
        # 原生按钮必须保留参与布局（NoButtons 会把 lineEdit 挤成 1px），只是视觉上透明
        assert (dialog._walk_speed.buttonSymbols()
                == QAbstractSpinBox.ButtonSymbols.UpDownArrows)

    def should_open_inside_workarea_when_bounds_injected(self, qtbot, base_config,
                                                         fake_screen):
        dialog = SettingsDialog(base_config, screen_bounds=fake_screen)
        qtbot.addWidget(dialog)
        x1, y1, x2, y2 = fake_screen.geometry()

        assert x1 <= dialog.x() and dialog.x() + dialog.width() <= x2 + 1
        assert y1 <= dialog.y() and dialog.y() + dialog.height() <= y2 + 1


def _wheel(widget, delta=-120):
    """往控件上送一次真实滚轮事件；Qt 会把 ignore 的事件冒泡给父级。"""
    event = QWheelEvent(QPointF(5, 5), QPointF(50, 50), QPoint(0, 0),
                        QPoint(0, delta), Qt.NoButton, Qt.NoModifier,
                        Qt.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)
    return event


class TestSystemConfigSection:
    """系统配置分区：非宠物参数（日志级别 / 分割阈值 / 保留天数 / 分档开关）。"""

    def should_load_system_settings_into_controls(self, qtbot):
        base = AppConfig.from_dict({"system": {"log_level": "warning",
                                               "log_max_mb": 42,
                                               "log_retention_days": 9,
                                               "log_split_by_level": False}})
        dialog = SettingsDialog(base)
        qtbot.addWidget(dialog)

        assert dialog._log_level.currentData() == "warning"
        assert dialog._log_max_mb.value() == 42
        assert dialog._log_retention.value() == 9
        assert dialog._log_split.isChecked() is False

    def should_collect_system_settings_without_losing_other_sections(self, qtbot,
                                                                     base_config):
        dialog = SettingsDialog(base_config)
        qtbot.addWidget(dialog)
        dialog._log_level.setCurrentIndex(dialog._log_level.findData("debug"))
        dialog._log_max_mb.setValue(25)
        dialog._log_retention.setValue(7)
        dialog._log_split.setChecked(False)

        cfg, error = dialog.collect()

        assert error == ""
        assert cfg.system.log_level == "debug"
        assert cfg.system.log_max_mb == 25
        assert cfg.system.log_retention_days == 7
        assert cfg.system.log_split_by_level is False
        assert cfg.ai.api_key == base_config.ai.api_key
        assert cfg.pet.skin == base_config.pet.skin


class TestReminderControlsClarity:
    """真机反馈「改完不生效」：实为滚轮不改值 + 复选框无对勾看不出状态。"""

    def should_spell_out_checkbox_state_in_text(self, qtbot, base_config):
        dialog = SettingsDialog(base_config)
        qtbot.addWidget(dialog)

        assert dialog._water_enabled.text() == "已启用"
        assert dialog._sedentary_enabled.text() == "已启用"

        dialog._water_enabled.setChecked(False)

        assert dialog._water_enabled.text() == "未启用"

    def should_explain_how_to_change_number_without_wheel(self, qtbot, base_config):
        dialog = SettingsDialog(base_config)
        qtbot.addWidget(dialog)

        for widget in (dialog._water_interval, dialog._sedentary_interval,
                       dialog._temperature, dialog._timeout):
            assert "滚轮" in widget.toolTip()
            assert "键盘" in widget.toolTip()


class TestNumberInputClarity:
    """真机反馈「没法输入」：键盘本身可用，是透明微调按钮让人找不到操作区。"""

    def should_accept_keyboard_input_in_number_fields(self, qtbot, base_config):
        dialog = SettingsDialog(base_config)
        qtbot.addWidget(dialog)
        spin = dialog._water_interval

        spin.setFocus()
        spin.selectAll()
        qtbot.keyClicks(spin, "30")

        assert spin.value() == 30

    def should_render_stepper_buttons_visibly(self, qtbot, base_config):
        """QSS ::up-arrow 的 border 三角形在 Qt 里渲染成深色方块，改用自绘图标按钮。"""
        dialog = SettingsDialog(base_config)
        qtbot.addWidget(dialog)
        spin = dialog._water_interval

        assert (spin.buttonSymbols()
                == QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        assert spin.text()                       # 编辑区没被挤没
        up = spin.findChild(QToolButton, "stepUp")
        down = spin.findChild(QToolButton, "stepDown")
        assert up is not None and down is not None
        assert not up.icon().isNull() and not down.icon().isNull()
        assert "QToolButton#stepUp" in dialog.styleSheet()
        assert "border-bottom: 5px solid" not in dialog.styleSheet()

        before = spin.value()
        up.click()
        assert spin.value() == before + 1
        down.click()
        assert spin.value() == before

    def should_hint_that_typing_directly_is_supported(self, qtbot, base_config):
        dialog = SettingsDialog(base_config)
        qtbot.addWidget(dialog)

        assert "直接" in dialog._water_interval.toolTip()
        assert "键入" in dialog._water_interval.toolTip()


class TestWheelGuard:
    """FR v1.14 C24：滚轮在设置窗只滚页面，绝不偷偷改表单值。"""

    def should_not_change_values_when_wheel_scrolled_over_inputs(self, qtbot, base_config):
        dialog = SettingsDialog(base_config)
        qtbot.addWidget(dialog)
        widgets = (dialog._temperature, dialog._timeout, dialog._walk_speed,
                   dialog._idle_min, dialog._idle_max, dialog._max_turns,
                   dialog._water_interval, dialog._sedentary_interval, dialog._skin)

        def snapshot():
            return [w.value() if hasattr(w, "value") else w.currentIndex()
                    for w in widgets]

        before = snapshot()
        for widget in widgets:
            _wheel(widget)

        assert snapshot() == before

    def should_scroll_page_instead_of_changing_value(self, qtbot, base_config):
        dialog = SettingsDialog(base_config)
        qtbot.addWidget(dialog)
        dialog.show()
        bar = dialog._scroll.verticalScrollBar()
        assert bar.maximum() > 0
        bar.setValue(0)

        _wheel(dialog._timeout, -120)

        assert bar.value() > 0
