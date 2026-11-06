"""序列帧动画：按动作帧率驱动 + 素材缺失占位渲染（PRD M1 / US-1 降级 / v2.0 C11）。

对外只暴露 set_action / set_facing / current_frame / start / stop，
渲染与状态判断解耦，便于 offscreen 环境冒烟测试。
"""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QTransform

from ..domain.actions import (
    FPS_BY_ACTION,
    FPS_FALLBACK,
    FRAME_FOOT_RATIO,
    FRAME_SOURCE_SIZE,
    STATE_TO_ACTION,
    action_for_state,
    frame_interval_ms,
)
from ..domain.position import Facing
from ..domain.state_machine import BehaviorState

logger = logging.getLogger(__name__)

#: 未知动作的兜底帧率（技术方案 D3：12FPS 兼顾观感与 CPU<3%）；表本体在 domain.actions
FPS = FPS_FALLBACK
FRAME_INTERVAL_MS = frame_interval_ms("")      # 未知动作名 = 走兜底帧率

#: 统一画布尺寸：素材尺寸不一致时等比缩放居中（PRD M1 边界）
CANVAS_SIZE = 128

#: 逐动作帧率与帧间隔的单一真相源在 `domain.actions`（app 层也按它算一次性动作的
#: 收场时长），本模块只引用不再写一份；行为状态 → 素材目录名同理
ACTION_BY_STATE = STATE_TO_ACTION

#: 占位渲染的脸部配色（按动作区分，便于肉眼确认状态机生效）
COLOR_BY_ACTION: dict[str, str] = {
    "idle": "#F2C18D",
    "walk": "#E9A86B",
    "run": "#E07B54",
    "stretch": "#F6D8A8",
    "hurt": "#F19C9C",
    "think": "#A8C8E8",
    "eat": "#D8B6E8",
    "speak": "#9FD8B4",
    "sleep": "#8E93B8",
    "sad": "#9AA0A6",
}


def _mirrored(pix: QPixmap) -> QPixmap:
    """向左时水平镜像：只翻 QPixmap，不为它多存一套帧文件。"""
    return pix.transformed(QTransform().scale(-1, 1))


class PlaceholderRenderer:
    """无素材时用 QPainter 画一只圆脸（PRD US-1：不崩溃、有形象）。"""

    def __init__(self, size: int = CANVAS_SIZE):
        self._size = size

    def pixmap(self, action: str, frame_index: int) -> QPixmap:
        pix = QPixmap(self._size, self._size)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing)
        try:
            self._paint_face(painter, action, frame_index)
        finally:
            painter.end()
        return pix

    def _paint_face(self, painter: QPainter, action: str, frame_index: int) -> None:
        s = self._size
        # 走动/待机时轻微上下浮动，让占位形象也有生命感
        bounce = 2 if action in ("walk", "run", "idle") and frame_index % 2 else 0
        body = QRectF(s * 0.15, s * 0.15 + bounce, s * 0.7, s * 0.7)
        painter.setBrush(QColor(COLOR_BY_ACTION.get(action, "#F2C18D")))
        painter.setPen(QPen(QColor("#6B4F3A"), max(1, s // 40)))
        painter.drawEllipse(body)

        eye_y = body.center().y() - body.height() * 0.12
        for dx in (-0.16, 0.16):
            cx = body.center().x() + body.width() * dx
            self._paint_eye(painter, cx, eye_y, body.width() * 0.09, action)
        self._paint_mouth(painter, body, action)

    @staticmethod
    def _paint_eye(painter: QPainter, cx: float, cy: float, r: float, action: str) -> None:
        pen = QPen(QColor("#3A2B21"), max(1, int(r)))
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        if action == "sleep":                       # 闭眼：一横
            painter.drawLine(int(cx - r), int(cy), int(cx + r), int(cy))
        elif action == "hurt":                      # 受戳：叉号
            painter.drawLine(int(cx - r), int(cy - r), int(cx + r), int(cy + r))
            painter.drawLine(int(cx - r), int(cy + r), int(cx + r), int(cy - r))
        elif action == "sad":                       # 低落：下垂眉
            painter.drawLine(int(cx - r), int(cy - r * 0.6), int(cx + r), int(cy + r * 0.4))
        else:
            painter.setBrush(QColor("#3A2B21"))
            painter.drawEllipse(int(cx - r / 2), int(cy - r / 2), int(r), int(r))

    @staticmethod
    def _paint_mouth(painter: QPainter, body: QRectF, action: str) -> None:
        cx, cy = body.center().x(), body.bottom() - body.height() * 0.24
        w = body.width() * 0.22
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor("#3A2B21"), max(1, int(body.width() / 30))))
        if action in ("eat", "speak", "hurt"):      # 张嘴：小圆
            painter.drawEllipse(int(cx - w / 2), int(cy - w / 2), int(w), int(w))
        elif action == "sad":                       # 嘴角向下
            painter.drawArc(int(cx - w), int(cy), int(w * 2), int(w), 200 * 16, 1400 * 16)
        else:                                       # 微笑
            painter.drawArc(int(cx - w), int(cy - w), int(w * 2), int(w * 2), 200 * 16, 1400 * 16)


class FrameAnimator(QObject):
    """帧序列播放器：动作切换即换帧表；缺素材走占位渲染。"""

    #: 每次定时器推进发出一次，携带当前帧 QPixmap
    frame_ready = Signal(object)

    def __init__(self, loader, size: int = CANVAS_SIZE, parent: QObject | None = None):
        super().__init__(parent)
        self._loader = loader
        self._size = size
        self._placeholder = PlaceholderRenderer(size)
        self._cache: dict[Path, QPixmap] = {}
        self._mirror_cache: dict[Path, QPixmap] = {}
        self._action = ""
        self._frames: list[Path] = []
        self._index = 0
        self._facing = Facing.RIGHT
        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_INTERVAL_MS)
        self._timer.timeout.connect(self._advance)

    # ---------- 控制 ----------

    def set_action(self, action: str) -> None:
        """切换动作：换帧表并改帧率；同名重复调用不重置帧序（避免跳帧）。"""
        if action == self._action:
            return
        self._action = action
        self._index = 0
        self._frames = self._load_frames(action)
        self._timer.setInterval(frame_interval_ms(action))  # 逐动作帧率（PRD v2.0 C11）
        self.frame_ready.emit(self.current_frame())

    def set_state(self, state: BehaviorState) -> None:
        self.set_action(action_for_state(state))

    def set_loader(self, loader) -> None:
        """热切换素材来源（换皮肤）：清帧缓存并重读当前动作。"""
        self._loader = loader
        self._cache.clear()
        self._mirror_cache.clear()
        action = self._action or "idle"
        self._action = ""            # 绕开 set_action 的同名短路，强制重取帧表
        self.set_action(action)

    def set_facing(self, facing: Facing) -> None:
        if facing is not self._facing:
            self._facing = facing
            self.frame_ready.emit(self.current_frame())

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    @property
    def is_running(self) -> bool:
        return self._timer.isActive()

    @property
    def interval_ms(self) -> int:
        """当前动作的帧间隔（毫秒）：per-action 帧率是否真生效可在此自检。"""
        return self._timer.interval()

    @property
    def action(self) -> str:
        return self._action

    @property
    def uses_placeholder(self) -> bool:
        """当前动作是否走占位渲染（素材缺失降级，PRD US-1）。"""
        return not self._frames

    # ---------- 帧内容 ----------

    def current_frame(self) -> QPixmap:
        if self._frames:
            return self._facing_frame(self._frames[self._index % len(self._frames)])
        pix = self._placeholder.pixmap(self._action or "idle", self._index)
        return _mirrored(pix) if self._facing is Facing.LEFT else pix

    def _facing_frame(self, path: Path) -> QPixmap:
        """按朝向取帧：镜像也入缓存，免得每拍重做一遍整幅变换。"""
        if self._facing is not Facing.LEFT:
            return self._frame_pixmap(path)
        cached = self._mirror_cache.get(path)
        if cached is None:
            cached = _mirrored(self._frame_pixmap(path))
            self._mirror_cache[path] = cached
        return cached

    def _advance(self) -> None:
        self._index += 1
        self.frame_ready.emit(self.current_frame())

    def _load_frames(self, action: str) -> list[Path]:
        try:
            return list(self._loader.frames(action))
        except Exception:  # noqa: BLE001 - 素材读取异常走占位，不崩（PRD US-1）
            logger.warning("frame load failed for action=%s, use placeholder", action,
                           exc_info=True)
            return []

    def _frame_pixmap(self, path: Path) -> QPixmap:
        cached = self._cache.get(path)
        if cached is not None:
            return cached
        pix = QPixmap()
        try:
            loaded = pix.load(str(path))
        except Exception:  # noqa: BLE001 - 损坏文件按加载失败处理
            loaded = False
            logger.warning("broken frame file: %s", path)
        if not loaded or pix.isNull():
            logger.warning("cannot read frame %s, fallback to placeholder", path)
            fallback = self._placeholder.pixmap(self._action or "idle", self._index)
            self._cache[path] = fallback
            return fallback
        scaled = self._fit_canvas(pix)
        self._cache[path] = scaled
        return scaled

    def _fit_canvas(self, pix: QPixmap) -> QPixmap:
        """尺寸不一致的帧等比缩放：横向居中，纵向按脚线贴画布底边（PRD M1 边界）。"""
        if pix.width() == pix.height() == self._size:
            return pix
        canvas = QPixmap(self._size, self._size)
        canvas.fill(Qt.transparent)
        scaled = pix.scaled(self._size, self._size,
                            Qt.KeepAspectRatio, Qt.SmoothTransformation)
        painter = QPainter(canvas)
        try:
            painter.drawPixmap(
                (self._size - scaled.width()) // 2,
                self._foot_y(pix, scaled), scaled)
        finally:
            painter.end()
        return canvas

    def _foot_y(self, src: QPixmap, scaled: QPixmap) -> int:
        """纵向落位：把脚线放到画布底边，而不是让整幅图几何居中。

        素材在 256 画布里把脚放在 y=216，脚下留了 40px 透明余量；等比缩到 128 后那
        余量变 20px，而窗口底边就是贴地模式的地面——居中等于让猫常年飘在半空。
        非方形或尺寸不足契约（多是测试图与手工小图）时无从知道脚线在哪，保持居中。
        """
        if src.width() != src.height() or src.height() < FRAME_SOURCE_SIZE:
            return (self._size - scaled.height()) // 2
        return self._size - round(FRAME_FOOT_RATIO * scaled.height())
