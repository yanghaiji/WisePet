"""命令面板：过滤、执行信号、Esc 关闭（offscreen 冒烟）。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from WisePet.ui.command_palette import CommandPalette

_COMMANDS = [
    ("reminder", "一句话提醒", "N 分钟后提醒我"),
    ("focus", "番茄钟", "专注 25 分钟"),
    ("search", "搜聊天记录", "跨日全文检索"),
    ("export", "导出聊天记录", "导出到本机 Markdown"),
]


def should_filter_commands_by_keyword(qtbot):
    palette = CommandPalette(_COMMANDS)
    qtbot.addWidget(palette)

    assert palette.visible_commands == [c[0] for c in _COMMANDS]
    palette.input_widget.setText("提醒")
    assert palette.visible_commands == ["reminder"]


def should_emit_command_id_and_hide_on_enter(qtbot):
    palette = CommandPalette(_COMMANDS)
    qtbot.addWidget(palette)
    seen: list[str] = []
    palette.executed.connect(seen.append)

    palette.input_widget.setText("番茄")
    QTest.keyClick(palette.input_widget, Qt.Key_Return)

    assert seen == ["focus"]
    assert palette.isVisible() is False


def should_close_on_escape(qtbot):
    palette = CommandPalette(_COMMANDS)
    qtbot.addWidget(palette)
    palette.show()

    QTest.keyClick(palette, Qt.Key_Escape)

    assert palette.isVisible() is False
