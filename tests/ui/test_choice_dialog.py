"""贴纸风三选一对话框（位置授权）回归。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton

from WisePet.ui.input_dialog import ChoiceDialog


def test_choice_dialog_is_frameless_sticker_card(qapp):
    dlg = ChoiceDialog("位置权限", "取城市不上传内容",
                       [("once", "仅本次允许"), ("always", "总是允许"),
                        ("deny", "拒绝")])
    assert dlg.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert dlg.isModal()
    assert dlg.selected is None


def test_choice_dialog_returns_clicked_value(qapp):
    dlg = ChoiceDialog("位置权限", "说明",
                       [("once", "仅本次"), ("always", "总是"), ("deny", "拒绝")])
    buttons = dlg.findChildren(QPushButton)
    assert len(buttons) == 3
    assert [b.text() for b in buttons] == ["仅本次", "总是", "拒绝"]
    dlg._choose("always")
    assert dlg.selected == "always"


def test_choice_dialog_button_click_sets_value(qapp, qtbot):
    dlg = ChoiceDialog("位置权限", "说明",
                       [("once", "仅本次"), ("always", "总是"), ("deny", "拒绝")])
    qtbot.addWidget(dlg)
    buttons = dlg.findChildren(QPushButton)
    qtbot.mouseClick(buttons[2], Qt.MouseButton.LeftButton)  # 拒绝
    assert dlg.selected == "deny"
