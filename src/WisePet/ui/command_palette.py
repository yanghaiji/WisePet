"""命令面板：一处搜到并执行桌宠的所有能力，替代继续往右键菜单堆项。

只负责展示与发信号；命令注册表与执行由组装根提供，ui 不碰业务（规约 3）。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QVBoxLayout, QWidget,
)

_STYLE = """
QFrame#paletteCard { background: #FFFDF7; border: 1px solid #DFC49A; border-radius: 16px; }
QLineEdit#paletteInput {
    background: #FFFFFF; border: 1px solid #E7D3B2; border-radius: 9px;
    padding: 8px 12px; color: #3A2B21; font-size: 13px;
    selection-background-color: #F3D9A8;
}
QLineEdit#paletteInput:focus { border: 1px solid #E8A45C; }
QLabel#paletteDot { color: #E8A45C; font-size: 16px; }
QLabel#paletteHint { color: #A08A6C; font-size: 11px; }
QListWidget {
    background: #FFFFFF; border: 1px solid #E7D3B2; border-radius: 9px;
    color: #3A2B21; font-size: 12px; padding: 4px;
}
QListWidget::item { padding: 7px 9px; border-radius: 6px; }
QListWidget::item:selected { background: #F8D79B; color: #5A3E28; }
"""


class CommandPalette(QWidget):
    """commands: [(id, 标题, 说明)]；回车/双击执行，Esc 关闭。"""

    executed = Signal(str)

    def __init__(self, commands: list[tuple[str, str, str]], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(460, 380)
        self.setStyleSheet(_STYLE)
        self._commands = list(commands)

        card = QFrame(self)
        card.setObjectName("paletteCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

        dot = QLabel("● 想做什么？", card)
        dot.setObjectName("paletteDot")
        self._input = QLineEdit(card)
        self._input.setObjectName("paletteInput")
        self._input.setPlaceholderText("输入关键词，如「提醒」「番茄钟」「搜聊天」")
        self._input.textChanged.connect(self._filter)
        self._input.returnPressed.connect(self._run_current)
        self._list = QListWidget(card)
        self._list.itemDoubleClicked.connect(lambda _item: self._run_current())
        self._list.currentRowChanged.connect(lambda _row: None)
        hint = QLabel("↑↓ 选择 · 回车执行 · Esc 关闭", card)
        hint.setObjectName("paletteHint")

        body = QVBoxLayout(card)
        body.setContentsMargins(18, 16, 18, 14)
        body.setSpacing(9)
        body.addWidget(dot)
        body.addWidget(self._input)
        body.addWidget(self._list, 1)
        body.addWidget(hint)
        self._filter("")

    # ---------- 对外 ----------

    @property
    def input_widget(self) -> QLineEdit:
        return self._input

    @property
    def visible_commands(self) -> list[str]:
        return [self._list.item(i).data(Qt.UserRole)
                for i in range(self._list.count())]

    def show_centered(self) -> None:
        screen = QApplication.primaryScreen()
        geometry = screen.availableGeometry() if screen else None
        if geometry is not None:
            self.move(geometry.center().x() - self.width() // 2,
                      geometry.center().y() - self.height() // 2)
        self.show()
        self.raise_()
        self._input.setFocus()
        self._input.selectAll()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt 重写
        if event.key() == Qt.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)

    # ---------- 内部 ----------

    def _filter(self, needle: str) -> None:
        query = str(needle or "").strip().lower()
        self._list.clear()
        for command_id, label, hint in self._commands:
            if query and query not in f"{label} {hint} {command_id}".lower():
                continue
            item = QListWidgetItem(f"{label}    · {hint}")
            item.setData(Qt.UserRole, command_id)
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)

    def _run_current(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        command_id = str(item.data(Qt.UserRole))
        self.hide()
        self.executed.emit(command_id)
