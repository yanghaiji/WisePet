"""截图区域选择器：屏幕冻结变暗，拖拽框选，松手出结果（Esc 取消）。

背景是进入选择器前抓好的屏幕快照——盖住桌面的同时，看起来就像画面被"冻住"。
只覆盖主屏（多屏场景抓的是主屏画面，见 main._screenshot_ask 注释）。
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

#: 选区最小尺寸：误点一下不该当成截图
_MIN_SELECTION = 8
#: 未选中区域的压暗透明度
_DIM_ALPHA = 110


class RegionSelector(QWidget):
    """全屏遮罩 + 橡皮筋框选；selected 携带屏幕坐标系下的选区。"""

    selected = Signal(QRect)
    cancelled = Signal()

    def __init__(self, snapshot: QPixmap, on_done: Callable[[], None] | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._snapshot = snapshot
        self._on_done = on_done
        self._origin: QPoint | None = None
        self._current: QPoint | None = None

        screen = QGuiApplication.primaryScreen()
        geometry = screen.geometry() if screen is not None else QRect(0, 0, 1280, 800)
        self.setGeometry(geometry)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

    # ---------- 交互 ----------

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 重写
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.position().toPoint()
            self._current = self._origin
            self.update()
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt 重写
        if self._origin is not None:
            self._current = event.position().toPoint()
            self.update()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 重写
        if event.button() != Qt.MouseButton.LeftButton or self._origin is None:
            event.accept()
            return
        rect = QRect(self._origin, event.position().toPoint()).normalized()
        self._origin = self._current = None
        self._finish(rect if rect.width() >= _MIN_SELECTION
                     and rect.height() >= _MIN_SELECTION else None)
        event.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt 重写
        if event.key() == Qt.Key.Key_Escape:
            self._finish(None)
            event.accept()
            return
        super().keyPressEvent(event)

    # ---------- 内部 ----------

    def _finish(self, rect: QRect | None) -> None:
        self.close()
        if self._on_done is not None:
            self._on_done()
        if rect is None:
            self.cancelled.emit()
        else:
            self.selected.emit(rect)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 重写
        painter = QPainter(self)
        try:
            # 冻结画面：快照铺满遮罩，让用户在"静止的桌面"上框选
            painter.drawPixmap(self.rect(), self._snapshot)
            selection = self._selection_rect()
            # 选区外压暗：四块矩形拼出挖洞效果（不做位图运算，代价最低）
            if selection is None or selection.isEmpty():
                painter.fillRect(self.rect(), QColor(0, 0, 0, _DIM_ALPHA // 2))
                if selection is not None:
                    self._draw_border(painter, selection)
                return
            w, h = self.width(), self.height()
            painter.fillRect(QRect(0, 0, w, selection.top()), QColor(0, 0, 0, _DIM_ALPHA))
            painter.fillRect(QRect(0, selection.bottom() + 1, w,
                                   h - selection.bottom() - 1),
                             QColor(0, 0, 0, _DIM_ALPHA))
            painter.fillRect(QRect(0, selection.top(), selection.left(),
                                   selection.height()),
                             QColor(0, 0, 0, _DIM_ALPHA))
            painter.fillRect(QRect(selection.right() + 1, selection.top(),
                                   w - selection.right() - 1, selection.height()),
                             QColor(0, 0, 0, _DIM_ALPHA))
            self._draw_border(painter, selection)
        finally:
            painter.end()

    def _selection_rect(self) -> QRect | None:
        if self._origin is None or self._current is None:
            return None
        return QRect(self._origin, self._current).normalized()

    @staticmethod
    def _draw_border(painter: QPainter, rect: QRect) -> None:
        painter.setPen(QColor(232, 164, 92))       # 主题橘
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
