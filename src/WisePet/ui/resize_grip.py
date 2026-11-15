"""无边框窗口的尺寸手柄：右下角自绘斜纹 + 拖拽缩放。

FramelessWindowHint 窗口没有系统边框；QSizeGrip 的默认灰点画法既丑又会被
后加入的卡片盖住收不到鼠标事件（真机踩过：手柄看得见但拖不动）。这里改为：
- QWidget 自绘三条主题色斜纹（hover 加深），视觉与贴纸风一致；
- 自己实现「按下记起点 → 移动改尺寸 → 松开发 user_resized」的缩放逻辑，
  窗口必须 raise_() 手柄保证它在卡片之上。
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

_NORMAL = QColor("#C9A96B")
_HOVER = QColor("#A9884F")


class ResizeGrip(QWidget):
    """右下角尺寸手柄：按住左键拖动缩放宿主窗口，松开发 user_resized。"""

    user_resized = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._min_size = QSize(120, 40)     # 宿主窗口可由组装层按需收紧
        self._axes = "both"                 # both / horizontal：输入条只让横向拖
        self._press_global: QPoint | None = None
        self._start_size: QSize | None = None

    @property
    def min_size(self) -> QSize:
        return self._min_size

    @min_size.setter
    def min_size(self, value: QSize) -> None:
        self._min_size = QSize(value)

    @property
    def axes(self) -> str:
        return self._axes

    @axes.setter
    def axes(self, value: str) -> None:
        self._axes = "horizontal" if value == "horizontal" else "both"
        self.setCursor(Qt.CursorShape.SizeHorCursor if self._axes == "horizontal"
                       else Qt.CursorShape.SizeFDiagCursor)

    # ---------- 缩放交互 ----------

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 重写
        if event.button() == Qt.MouseButton.LeftButton:
            window = self.window()
            self._press_global = event.globalPosition().toPoint()
            self._start_size = QSize(window.width(), window.height())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt 重写
        if self._press_global is None or self._start_size is None:
            return
        delta = event.globalPosition().toPoint() - self._press_global
        window = self.window()
        new_w = max(self._min_size.width(), self._start_size.width() + delta.x())
        new_h = (self._start_size.height() if self._axes == "horizontal"
                 else max(self._min_size.height(),
                          self._start_size.height() + delta.y()))
        window.resize(new_w, new_h)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 重写
        if event.button() == Qt.MouseButton.LeftButton and self._press_global is not None:
            self._press_global = None
            self._start_size = None
            self.user_resized.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # ---------- 外观 ----------

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 重写
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(_HOVER if self.underMouse() else _NORMAL, 1.8))
        w, h = self.width(), self.height()
        # 只拖宽度时画「双箭头」样式，别让人误以为能拉高
        if self._axes == "horizontal":
            painter.drawLine(3, h // 2, w - 3, h // 2)
            painter.drawLine(3, h // 2, 7, h // 2 - 3)
            painter.drawLine(3, h // 2, 7, h // 2 + 3)
            painter.drawLine(w - 3, h // 2, w - 7, h // 2 - 3)
            painter.drawLine(w - 3, h // 2, w - 7, h // 2 + 3)
        else:
            for i in range(3):
                offset = 5 + i * 4
                painter.drawLine(w - offset, h, w, h - offset)
        painter.end()
