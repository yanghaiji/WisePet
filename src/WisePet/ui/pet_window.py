"""宠物主窗口：无边框 / 透明 / 置顶，鼠标事件转 app 层命令（PRD M1/M3）。

窗口自身不做任何行为判断：拖拽是否算点击、落位钳制、状态迁移全由
PetController 决定；坐标一律基于虚拟桌面工作区（AGENT.md 规约 6）。
"""
from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import QPropertyAnimation, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..domain.config_model import AppConfig
from ..domain.product import GrowthDecoration
from .screen_bounds import ScreenBounds

logger = logging.getLogger(__name__)

#: 拖拽候选按钮：仅左键拖动，右键留给菜单（PRD US-2）
_DRAG_BUTTON = Qt.LeftButton


class PetWindow(QWidget):
    """宠物本体窗口。"""

    #: 原地按下并松开（未拖动）→ 主程序弹出聊天输入条
    clicked = Signal()
    #: 右键请求 → 主程序弹出菜单，携带全局坐标
    context_requested = Signal(QPoint)
    #: 宠物位置真的变了 → 聊天浮窗重新贴边停靠（FR v1.10 C18）
    moved = Signal()

    def __init__(self, controller, animator, config_provider: Callable[[], AppConfig],
                 screen_bounds: ScreenBounds, size: int = 128,
                 parent: QWidget | None = None,
                 growth_provider: Callable[[], GrowthDecoration] | None = None):
        super().__init__(parent)
        self._controller = controller
        self._animator = animator
        self._config = config_provider
        self._screen = screen_bounds
        self._growth_provider = growth_provider
        self._press_global = QPoint()
        self._last_global = QPoint()
        self._moved = False
        self._last_synced: tuple[int, int] | None = None

        # 无边框 + 置顶 + 透明背景 + 不占任务栏（macOS/Windows 均由 Qt 原生支持）
        # NoDropShadowWindowHint：Windows 默认给无边框窗加一圈投影，那圈会被渲染成
        # 半透明白边，走动时随窗口移动就成了一道白框（实测真机闪白的主因之一）。
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        # 禁掉系统背景擦除：否则 Qt 在 paintEvent 前先用窗口底色（白）擦一遍，
        # move() 后、猫画上前那一帧就露白。透明窗必须自己保证画布初始全透明。
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setFixedSize(size, size)
        self._animator.frame_ready.connect(self._on_frame)

        # 柔和瞬移动画链：淡出 → 落位 → 淡入（roam 模式「出现在任何地方」不生硬）
        self._jump_target: tuple[int, int] | None = None
        self._jump_on_done: Callable[[], None] | None = None
        self._fade_out = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade_out.setDuration(220)
        self._fade_out.setStartValue(1.0)
        self._fade_out.setEndValue(0.0)
        self._fade_out.finished.connect(self._apply_soft_jump)
        self._fade_in = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade_in.setDuration(320)
        self._fade_in.setEndValue(1.0)
        self._fade_in.finished.connect(self._finish_soft_jump)

    # ---------- 渲染 ----------

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 重写
        painter = QPainter(self)
        try:
            # 先把整块画布刷成全透明（Source 模式直接覆盖 alpha 通道，不叠加），
            # 再画猫：保证猫以外的像素永远是透明的，绝不会残留白底。
            painter.setCompositionMode(QPainter.CompositionMode_Source)
            painter.fillRect(self.rect(), Qt.transparent)
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            frame = self._animator.current_frame()
            decoration = (self._growth_provider()
                          if self._growth_provider is not None else None)
            if decoration is None or not decoration.visible:
                painter.drawPixmap(0, 0, frame)
            else:
                self._draw_with_growth(painter, frame, decoration)
        finally:
            painter.end()

    def _draw_with_growth(self, painter: QPainter, frame, decoration) -> None:
        """等级带来的可见差异：以脚线为锚整体缩放 + 头顶等级徽章。

        不改素材文件（换素材要重跑导入管线，是当前已知瓶颈），只在绘制时叠加。
        """
        size = self.width()
        painter.save()
        painter.translate(size / 2, size)
        painter.scale(decoration.scale, decoration.scale)
        painter.drawPixmap(int(-size / 2), -size, frame)
        painter.restore()
        if not (decoration.ring or decoration.badge):
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        badge_rect = QRect(int(size * 0.30), 4, int(size * 0.40), 16)
        painter.setPen(QPen(QColor(decoration.ring or "#E8A45C"), 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(badge_rect, 8, 8)
        if decoration.badge:
            painter.setPen(QColor("#6B4F3A"))
            font = painter.font()
            font.setPointSize(8)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(badge_rect, Qt.AlignCenter, decoration.badge)

    def _on_frame(self, _pixmap) -> None:
        self.update()

    # ---------- 位置同步 ----------

    def sync_from_pet(self) -> None:
        """把 domain 的位置/朝向/动作刷到窗口；位置变了才通知聊天浮窗跟随。"""
        pet = self._controller.pet
        position = (pet.position.x, pet.position.y)
        self.move(*position)
        self._animator.set_state(pet.state)
        self._animator.set_facing(pet.facing)
        self.update()
        if position != self._last_synced:
            self._last_synced = position
            self.moved.emit()

    def clamp_into_screen(self) -> None:
        """分辨率变化后重新钳制位置（PRD M1 边界项）。"""
        pet = self._controller.pet
        self._controller.move_to(pet.position.x, pet.position.y)
        self.sync_from_pet()

    # ---------- 柔和瞬移（roam 模式） ----------

    def soft_jump_to(self, x: int, y: int,
                     on_done: Callable[[], None] | None = None) -> None:
        """原地淡出后挪到目标点再淡入：瞬移可见但不生硬。"""
        self._fade_in.stop()
        self._jump_target = (x, y)
        self._jump_on_done = on_done
        self._fade_out.start()

    def _apply_soft_jump(self) -> None:
        """淡出完成：此刻落位，用户只看到「凭空出现」。"""
        target, self._jump_target = self._jump_target, None
        if target is not None:
            self._controller.move_to(*target)
            self.sync_from_pet()
        self._fade_in.setStartValue(float(self.windowOpacity()))
        self._fade_in.start()

    def _finish_soft_jump(self) -> None:
        """淡入完成：通知控制器解除瞬移冻结。"""
        self.sync_from_pet()
        callback, self._jump_on_done = self._jump_on_done, None
        if callback is not None:
            callback()

    # ---------- 鼠标交互（PRD US-2） ----------

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 重写
        if event.button() == _DRAG_BUTTON:
            global_pos = event.globalPosition().toPoint()
            self._press_global = global_pos
            self._last_global = global_pos
            self._moved = False
            self._controller.on_press()   # 走动中被打断、进入拖拽候选都由 app 决定
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt 重写
        if not (event.buttons() & _DRAG_BUTTON):
            return
        global_pos = event.globalPosition().toPoint()
        if not self._moved and self._manhattan(global_pos, self._press_global) < \
                self._click_threshold():
            return  # 位移未超阈值：仍视为点击候选，不启动位移
        self._moved = True
        delta = global_pos - self._last_global
        self._last_global = global_pos
        self._controller.on_drag(delta.x(), delta.y())
        self.sync_from_pet()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 重写
        if event.button() != _DRAG_BUTTON:
            return
        self._controller.on_release()     # 落位钳制 + 「原地松手即被戳」由 app 判定
        self.sync_from_pet()
        if not self._moved:
            self.clicked.emit()
        event.accept()

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt 重写
        self.context_requested.emit(event.globalPos())
        event.accept()

    # ---------- 内部 ----------

    def _click_threshold(self) -> int:
        return int(self._config().pet.click_move_threshold_px)

    @staticmethod
    def _manhattan(a: QPoint, b: QPoint) -> int:
        return abs(a.x() - b.x()) + abs(a.y() - b.y())

    def bubble_anchor(self) -> QPoint:
        """气泡挂靠点：宠物头顶中部（全局坐标）。"""
        geo = self.frameGeometry()
        return QPoint(geo.center().x(), geo.top() - 4)

    def window_rect(self) -> QRect:
        """宠物全局矩形（QRect，含端点）：取 domain 位置，不依赖窗口管理器。"""
        pet = self._controller.pet
        return QRect(pet.position.x, pet.position.y, self.width(), self.height())
