"""陪伴统计面板：陪你多少天、聊了多少句、今天专注多久（v2.0 数据面板）。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

_STYLE = """
QFrame#statsCard {
    background: #FFFDF7;
    border: 1px solid #DFC49A;
    border-radius: 16px;
}
QLabel#statsTitle { color: #5A3E28; font-size: 15px; font-weight: 600; }
QLabel#statsDot { color: #E8A45C; font-size: 16px; }
QLabel#statValue { color: #E8A45C; font-size: 26px; font-weight: 700; }
QLabel#statLabel { color: #9A7A55; font-size: 11px; }
QLabel#statsFoot { color: #B09A7C; font-size: 10px; }
QPushButton#close {
    background: transparent; border: none; color: #A08A6C;
    font-size: 18px; border-radius: 10px; min-width: 26px; min-height: 26px;
}
QPushButton#close:hover { background: #F3E1C6; color: #6B4F3A; }
"""


class StatsDialog(QWidget):
    """右键 →「陪伴统计…」：纯本地数据，只读展示。"""

    def __init__(self, screen_bounds=None, parent: QWidget | None = None):
        super().__init__(parent)
        self._screen = screen_bounds
        self.setWindowTitle("WisePet 陪伴统计")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(380)
        self.setStyleSheet(_STYLE)

        card = QFrame(self)
        card.setObjectName("statsCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

        dot = QLabel("●", card)
        dot.setObjectName("statsDot")
        title = QLabel("陪伴统计", card)
        title.setObjectName("statsTitle")
        close_btn = QPushButton("×", card)
        close_btn.setObjectName("close")
        close_btn.clicked.connect(self.hide)

        head = QHBoxLayout()
        head.setContentsMargins(18, 14, 12, 4)
        head.setSpacing(6)
        head.addWidget(dot)
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(close_btn)

        self._values: dict[str, QLabel] = {}
        body = QVBoxLayout(card)
        body.setContentsMargins(0, 0, 0, 12)
        body.addLayout(head)
        grid = QHBoxLayout()
        grid.setSpacing(8)
        for key, label in (("days", "陪伴天数"), ("messages", "累计消息"),
                           ("focus_min", "今日专注(分)"), ("focus_count", "今日专注(次)")):
            cell = QVBoxLayout()
            value = QLabel("–", card)
            value.setObjectName("statValue")
            value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            caption = QLabel(label, card)
            caption.setObjectName("statLabel")
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cell.addStretch(1)
            cell.addWidget(value)
            cell.addWidget(caption)
            cell.addStretch(1)
            self._values[key] = value
            holder = QFrame(card)
            holder.setLayout(cell)
            grid.addWidget(holder, 1)
        grid_holder = QWidget(card)
        grid_holder.setLayout(grid)
        body.addWidget(grid_holder)

        foot = QLabel("数据全部保存在本机 ~/.WisePet/，绝不上传", card)
        foot.setObjectName("statsFoot")
        foot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.addWidget(foot)

    def set_values(self, *, days: int, messages: int,
                   focus_minutes: float, focus_count: int) -> None:
        self._values["days"].setText(str(days))
        self._values["messages"].setText(str(messages))
        self._values["focus_min"].setText(f"{focus_minutes:.0f}")
        self._values["focus_count"].setText(str(focus_count))

    def show_centered(self) -> None:
        self.adjustSize()
        if self._screen is not None:
            x1, y1, x2, y2 = self._screen.geometry()
            self.move(x1 + (x2 - x1 - self.width()) // 2,
                      y1 + (y2 - y1 - self.height()) // 3)
        self.show()
        self.raise_()
