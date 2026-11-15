"""「我记得你什么」对话框：一页看全本机数据，勾选即可真正删掉。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
    QVBoxLayout, QWidget,
)

_STYLE = """
QFrame#memoryCard {
    background: #FFFDF7; border: 1px solid #DFC49A; border-radius: 16px;
}
QLabel#memoryTitle { color: #5A3E28; font-size: 15px; font-weight: 600; }
QLabel#memoryDot { color: #E8A45C; font-size: 16px; }
QLabel#memoryHint { color: #9A7A55; font-size: 11px; }
QListWidget#memoryList {
    background: #FFFFFF; border: 1px solid #EAD8B8; border-radius: 10px;
    color: #3A2B21; font-size: 11px; padding: 4px;
}
QListWidget#memoryList::item { padding: 5px 6px; border-radius: 6px; }
QListWidget#memoryList::item:selected { background: #F7E7CC; color: #6B4F3A; }
QPushButton#memoryBtn {
    background: #F8D79B; border: 1px solid #E7C79A; border-radius: 9px;
    color: #6B4F3A; font-size: 11px; padding: 5px 12px;
}
QPushButton#memoryBtn:hover { background: #F3D9A8; }
QPushButton#memoryBtn:disabled { background: #F2EADB; color: #B6A489; }
QPushButton#close {
    background: transparent; border: none; color: #A08A6C;
    font-size: 18px; border-radius: 10px; min-width: 26px; min-height: 26px;
}
QPushButton#close:hover { background: #F3E1C6; color: #6B4F3A; }
"""

#: Qt 角色：条目对应的时间线 id
_ID_ROLE = Qt.ItemDataRole.UserRole


class MemoryDialog(QWidget):
    """只读聚合 + 可勾选删除；所有删除都通过 MemoryTimeline.forget() 落盘。"""

    def __init__(self, timeline, *, export_path: Path | str | None = None,
                 screen_bounds=None, parent: QWidget | None = None):
        super().__init__(parent)
        self._timeline = timeline
        self._export_path = Path(export_path) if export_path else None
        self._screen = screen_bounds
        self.setWindowTitle("WisePet 我记得你什么")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(560, 460)
        self.setStyleSheet(_STYLE)

        card = QFrame(self)
        card.setObjectName("memoryCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

        dot = QLabel("●", card)
        dot.setObjectName("memoryDot")
        title = QLabel("我记得你什么", card)
        title.setObjectName("memoryTitle")
        close_btn = QPushButton("×", card)
        close_btn.setObjectName("close")
        close_btn.clicked.connect(self.hide)
        head = QHBoxLayout()
        head.setContentsMargins(16, 12, 10, 4)
        head.setSpacing(6)
        head.addWidget(dot)
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(close_btn)

        hint = QLabel("全部存在本机，勾选后可真正删除；不上传、不画像。", card)
        hint.setObjectName("memoryHint")
        self._list = QListWidget(card)
        self._list.setObjectName("memoryList")
        self._list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)

        self._delete_btn = QPushButton("删除所选", card)
        self._delete_btn.setObjectName("memoryBtn")
        self._delete_btn.clicked.connect(self._delete_selected)
        self._export_btn = QPushButton("导出清单", card)
        self._export_btn.setObjectName("memoryBtn")
        self._export_btn.clicked.connect(self._export)
        refresh = QPushButton("刷新", card)
        refresh.setObjectName("memoryBtn")
        refresh.clicked.connect(self.reload)
        self._status = QLabel("", card)
        self._status.setObjectName("memoryHint")
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(self._delete_btn)
        buttons.addWidget(self._export_btn)
        buttons.addWidget(refresh)
        buttons.addStretch(1)
        buttons.addWidget(self._status)

        body = QVBoxLayout(card)
        body.setContentsMargins(16, 0, 16, 14)
        body.setSpacing(8)
        body.addLayout(head)
        body.addWidget(hint)
        body.addWidget(self._list, 1)
        body.addLayout(buttons)
        self.reload()

    # ---------- 行为 ----------

    def reload(self) -> None:
        self._list.clear()
        for entry in self._timeline.entries():
            item = QListWidgetItem(entry.line(), self._list)
            item.setData(_ID_ROLE, entry.id)
            if not entry.removable:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self._status.setText(f"共 {self._list.count()} 项")

    def selected_ids(self) -> list[str]:
        return [item.data(_ID_ROLE) for item in self._list.selectedItems()
                if item.data(_ID_ROLE)]

    def _delete_selected(self) -> None:
        ids = self.selected_ids()
        if not ids:
            self._status.setText("先勾选要忘掉的内容")
            return
        count = self._timeline.forget(ids)
        self.reload()
        self._status.setText(f"已删除 {count} 项")

    def _export(self) -> None:
        if self._export_path is None:
            self._status.setText("未配置导出路径")
            return
        try:
            self._export_path.parent.mkdir(parents=True, exist_ok=True)
            self._export_path.write_text(self._timeline.export_text(),
                                         encoding="utf-8")
            self._status.setText(f"已导出到 {self._export_path.name}")
        except OSError:
            self._status.setText("导出失败")

    def show_centered(self) -> None:
        if self._screen is not None:
            x1, y1, x2, y2 = self._screen.geometry()
            self.move(x1 + (x2 - x1 - self.width()) // 2,
                      y1 + (y2 - y1 - self.height()) // 3)
        self.show()
        self.raise_()
