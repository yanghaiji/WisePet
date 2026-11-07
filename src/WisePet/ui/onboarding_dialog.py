"""首次启动引导卡：30 秒讲清「活物 + 助理」，并现场兑现两个首次价值时刻。

与聊天卡片 / 统计面板同一套无边框圆角贴纸风；只构造不自动显示，
由组装根判定「首次运行」后延迟弹出，offscreen 测试不传标记就完全不出现。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

_STYLE = """
QFrame#onboardCard {
    background: #FFFDF7; border: 1px solid #DFC49A; border-radius: 16px;
}
QLabel#onboardDot { color: #E8A45C; font-size: 16px; }
QLabel#onboardTitle { color: #3A2B21; font-size: 17px; font-weight: 600; }
QLabel#onboardBody { color: #6B4F3A; font-size: 12px; }
QLabel#onboardStep { color: #A08A6C; font-size: 11px; }
QPushButton#onboardNext {
    background: #F8D79B; border: 1px solid #E7C79A; border-radius: 9px;
    color: #5A3E28; font-size: 12px; font-weight: 600; padding: 7px 18px;
}
QPushButton#onboardNext:hover { background: #F3D9A8; }
QPushButton#onboardSkip {
    background: transparent; border: none; color: #A08A6C; font-size: 11px;
}
QPushButton#onboardSkip:hover { color: #6B4F3A; }
"""

#: (标题, 正文, 主按钮文案, 是否触发一次体验)
_STEPS: tuple[tuple[str, str, str, str], ...] = (
    ("你好，我是住在桌面的小墨",
     "不用注册、不用配 API Key，点我一下就能聊天；说的话只留在这台电脑里。",
     "跟我打个招呼", "chat"),
    ("我也会替你记着事",
     "试试「1 分钟后提醒我喝水」这类一句话吩咐，到点我真的会来叫你。"
     "番茄钟、久坐与喝水提醒都在右键菜单。",
     "试一个提醒", "reminder"),
    ("就这些，去忙吧",
     "右键可以翻历史对话、换皮肤、看陪伴统计；不想被打扰时开启「免打扰」。"
     "我会自己在桌面上溜达、打盹、发呆。",
     "开始使用", "done"),
)


class OnboardingDialog(QWidget):
    """三步引导；每步的 CTA 都真的做一件事，而不是只翻页。"""

    try_chat = Signal()
    try_reminder = Signal()
    completed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedWidth(420)
        self.setStyleSheet(_STYLE)
        self._step = 0

        card = QFrame(self)
        card.setObjectName("onboardCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

        dot = QLabel("●", card)
        dot.setObjectName("onboardDot")
        self._title = QLabel(card)
        self._title.setObjectName("onboardTitle")
        self._step_label = QLabel(card)
        self._step_label.setObjectName("onboardStep")
        self._body = QLabel(card)
        self._body.setObjectName("onboardBody")
        self._body.setWordWrap(True)
        self._next = QPushButton(card)
        self._next.setObjectName("onboardNext")
        self._next.setCursor(Qt.PointingHandCursor)
        self._next.clicked.connect(self._advance)
        self._skip = QPushButton("跳过引导", card)
        self._skip.setObjectName("onboardSkip")
        self._skip.setCursor(Qt.PointingHandCursor)
        self._skip.clicked.connect(self._finish)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(dot)
        head.addWidget(self._title, 1)
        head.addWidget(self._step_label)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.addStretch(1)
        buttons.addWidget(self._skip)
        buttons.addWidget(self._next)

        body = QVBoxLayout(card)
        body.setContentsMargins(20, 18, 20, 18)
        body.setSpacing(12)
        body.addLayout(head)
        body.addWidget(self._body)
        body.addLayout(buttons)
        self._render()

    # ---------- 对外 ----------

    @property
    def current_step(self) -> int:
        return self._step

    def show_centered(self) -> None:
        screen = QApplication.primaryScreen()
        geometry = screen.availableGeometry() if screen else None
        if geometry is not None:
            self.move(geometry.center().x() - self.width() // 2,
                      geometry.center().y() - self.height() // 2)
        self.show()
        self.raise_()

    # ---------- 内部 ----------

    def _render(self) -> None:
        title, text, cta, _kind = _STEPS[self._step]
        self._title.setText(title)
        self._body.setText(text)
        self._step_label.setText(f"{self._step + 1} / {len(_STEPS)}")
        self._next.setText(cta)
        self._skip.setVisible(self._step < len(_STEPS) - 1)

    def _advance(self) -> None:
        _title, _text, _cta, kind = _STEPS[self._step]
        if kind == "chat":
            self.try_chat.emit()
        elif kind == "reminder":
            self.try_reminder.emit()
        if self._step >= len(_STEPS) - 1:
            self._finish()
            return
        self._step += 1
        self._render()

    def _finish(self) -> None:
        self.completed.emit()
        self.hide()
