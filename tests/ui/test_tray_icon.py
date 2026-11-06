"""系统托盘冒烟：宠物藏起来之后仍要有一个能把它找回来的入口（FR v1.11 C21）。"""
from __future__ import annotations

import pytest
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from WisePet.ui.tray_icon import (
    TOOLTIP_HIDDEN,
    TOOLTIP_VISIBLE,
    PetTrayIcon,
)


def _icon(qapp, on_activate=lambda: None):
    menu = QMenu()
    tray = PetTrayIcon(menu, QPixmap(32, 32), on_activate, parent=qapp)
    return tray, menu


def should_hold_menu_and_stay_visible_when_built(qapp, qtbot):
    tray, menu = _icon(qapp)
    qtbot.addWidget(menu)

    assert tray.contextMenu() is menu
    assert tray.isVisible()
    assert tray.toolTip() == TOOLTIP_VISIBLE


def should_switch_tooltip_when_pet_hidden(qapp):
    tray, _ = _icon(qapp)

    tray.set_hidden_state(hidden=True)
    assert tray.toolTip() == TOOLTIP_HIDDEN

    tray.set_hidden_state(hidden=False)
    assert tray.toolTip() == TOOLTIP_VISIBLE


@pytest.mark.parametrize("reason", [QSystemTrayIcon.ActivationReason.Trigger,
                                    QSystemTrayIcon.ActivationReason.DoubleClick])
def should_call_activate_when_icon_clicked(qapp, reason):
    calls = []
    tray, _ = _icon(qapp, lambda: calls.append(1))

    tray.activated.emit(reason)

    assert calls == [1]


def should_ignore_right_click_when_icon_activated(qapp):
    """右键要留给托盘菜单，不能同时把宠物显隐又翻一次。"""
    calls = []
    tray, _ = _icon(qapp, lambda: calls.append(1))

    tray.activated.emit(QSystemTrayIcon.ActivationReason.Context)

    assert calls == []


def should_report_platform_support_when_asked():
    assert isinstance(PetTrayIcon.is_available(), bool)
