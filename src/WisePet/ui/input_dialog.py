"""贴纸风输入对话框：PIN / 身份名等轻输入的统一 UI（替代原生 QInputDialog）。

- 支持密码模式（圆点回显）与普通文本模式；
- `validator(text) -> (ok, error)`：确定时先校验，通过才 accept；
  校验失败错误信息显示在卡片内红字条上，不弹二次窗；
- 视觉与身份选择页同一套贴纸风。
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
    QWidget,
)

_STYLE = """
QFrame#inputCard {
    background: #FFFDF7; border: 1px solid #DFC49A; border-radius: 16px;
}
QLabel#inputTitle { color: #5A3E28; font-size: 15px; font-weight: 600; }
QLabel#inputHint { color: #9A7A55; font-size: 11px; }
QLabel#inputError { color: #C25B4E; font-size: 11px; }
QLineEdit#inputField {
    background: #FFFFFF; border: 1px solid #EAD8B8; border-radius: 10px;
    color: #3A2B21; padding: 8px 10px; font-size: 14px;
}
QPushButton#inputOk {
    background: #F8D79B; border: 1px solid #E7C79A; border-radius: 10px;
    color: #6B4F3A; padding: 7px 18px; font-size: 12px;
}
QPushButton#inputOk:hover { background: #F3D9A8; }
QPushButton#inputCancel {
    background: transparent; border: 1px solid #E2CFAF; border-radius: 10px;
    color: #9A7A55; padding: 7px 14px; font-size: 12px;
}
QPushButton#inputCancel:hover { background: #F3E1C6; }
"""


class InputDialog(QDialog):
    """单行输入；确定时走 validator，通过才关闭。"""

    def __init__(self, title: str, hint: str = "", *, password: bool = False,
                 validator: Callable[[str], tuple[bool, str]] | None = None,
                 placeholder: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self._validator = validator
        self.value: str | None = None      # None = 取消
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.WindowType.Dialog
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.FramelessWindowHint)
        self.setModal(True)
        self.setFixedWidth(380)
        self.setStyleSheet(_STYLE)

        card = QFrame(self)
        card.setObjectName("inputCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

        body = QVBoxLayout(card)
        body.setContentsMargins(18, 16, 18, 14)
        body.setSpacing(8)
        title_label = QLabel(title, card)
        title_label.setObjectName("inputTitle")
        body.addWidget(title_label)
        if hint:
            hint_label = QLabel(hint, card)
            hint_label.setObjectName("inputHint")
            hint_label.setWordWrap(True)
            body.addWidget(hint_label)

        self._field = QLineEdit(card)
        self._field.setObjectName("inputField")
        self._field.setEchoMode(QLineEdit.EchoMode.Password if password
                                else QLineEdit.EchoMode.Normal)
        self._field.setPlaceholderText(placeholder)
        self._field.returnPressed.connect(self._confirm)
        body.addWidget(self._field)

        self._error = QLabel("", card)
        self._error.setObjectName("inputError")
        self._error.setWordWrap(True)
        self._error.hide()
        body.addWidget(self._error)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("取消", card)
        cancel.setObjectName("inputCancel")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("确定", card)
        ok.setObjectName("inputOk")
        ok.clicked.connect(self._confirm)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        body.addLayout(buttons)

    def _confirm(self) -> None:
        text = self._field.text().strip()
        if self._validator is not None:
            ok, error = self._validator(text)
            if not ok:
                self._error.setText(error)
                self._error.show()
                return
        self.value = text
        self.accept()

    def show_centered(self) -> None:
        screen = self.screen() or self._screen_fallback()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(geo.x() + (geo.width() - self.width()) // 2,
                      geo.y() + (geo.height() - self.height()) // 3)
        self.show()
        self.raise_()
        self._field.setFocus()

    def _screen_fallback(self):
        from PySide6.QtGui import QGuiApplication
        return QGuiApplication.primaryScreen()


class ConfirmDialog(QDialog):
    """贴纸风确认框（替代系统 QMessageBox.question/information）。

    danger=True 时确定按钮红色警示（删除/重置类操作）；
    单按钮模式（confirm_text 传 None）用于纯提示。
    """

    def __init__(self, title: str, text: str, *, danger: bool = False,
                 confirm_text: str = "确定",
                 cancel_text: str | None = "取消",
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._cancel_text = cancel_text
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.WindowType.Dialog
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.FramelessWindowHint)
        self.setModal(True)
        self.setFixedWidth(380)
        self.setStyleSheet(_STYLE)

        card = QFrame(self)
        card.setObjectName("inputCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

        body = QVBoxLayout(card)
        body.setContentsMargins(18, 16, 18, 14)
        body.setSpacing(10)
        title_label = QLabel(title, card)
        title_label.setObjectName("inputTitle")
        title_label.setWordWrap(True)
        body.addWidget(title_label)
        text_label = QLabel(text, card)
        text_label.setObjectName("inputHint")
        text_label.setWordWrap(True)
        body.addWidget(text_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        if cancel_text:
            cancel = QPushButton(cancel_text, card)
            cancel.setObjectName("inputCancel")
            cancel.clicked.connect(self.reject)
            buttons.addWidget(cancel)
        ok = QPushButton(confirm_text, card)
        ok.setObjectName("inputError" if danger else "inputOk")
        ok.clicked.connect(self.accept)
        buttons.addWidget(ok)
        body.addLayout(buttons)

    def show_centered(self) -> None:
        from PySide6.QtGui import QGuiApplication
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(geo.x() + (geo.width() - self.width()) // 2,
                      geo.y() + (geo.height() - self.height()) // 3)
        self.show()
        self.raise_()


class ChoiceDialog(QDialog):
    """贴纸风三选一对话框（替代系统 QMessageBox 的位置授权等多选场景）。

    choices: [(返回值, 按钮文案), ...]；`selected` 在点击后为对应返回值，
    关闭/取消为 None。视觉与 InputDialog/ConfirmDialog 同一套贴纸风。
    """

    def __init__(self, title: str, text: str, choices: list[tuple[str, str]],
                 *, parent: QWidget | None = None):
        super().__init__(parent)
        self.selected: str | None = None
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.WindowType.Dialog
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.FramelessWindowHint)
        self.setModal(True)
        self.setFixedWidth(400)
        self.setStyleSheet(_STYLE)

        card = QFrame(self)
        card.setObjectName("inputCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

        body = QVBoxLayout(card)
        body.setContentsMargins(18, 16, 18, 14)
        body.setSpacing(10)
        title_label = QLabel(title, card)
        title_label.setObjectName("inputTitle")
        body.addWidget(title_label)
        text_label = QLabel(text, card)
        text_label.setObjectName("inputHint")
        text_label.setWordWrap(True)
        body.addWidget(text_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        for index, (value, label) in enumerate(choices):
            btn = QPushButton(label, card)
            # 第一个按钮作为主操作（橘色），其余为次操作（描边）
            btn.setObjectName("inputOk" if index == 0 else "inputCancel")
            btn.clicked.connect(lambda _checked=False, v=value: self._choose(v))
            buttons.addWidget(btn)
        body.addLayout(buttons)

    def _choose(self, value: str) -> None:
        self.selected = value
        self.accept()

    def show_centered(self) -> None:
        from PySide6.QtGui import QGuiApplication
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(geo.x() + (geo.width() - self.width()) // 2,
                      geo.y() + (geo.height() - self.height()) // 3)
        self.show()
        self.raise_()
