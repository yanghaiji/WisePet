"""科技感出场/退场特效：粒子汇聚（spawn）/ 粒子消散（exit）+ 能量环。

独立置顶透明窗，盖在宠物位置上播放；spawn 结束后宠物淡入现身，
exit 与宠物淡出同步播放，结束后回调（隐藏窗口 / 退出程序）。
纯 QPainter 程序化绘制，30fps，无额外素材依赖。
"""
from __future__ import annotations

import math
import random

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget

#: 特效总时长与帧间隔（毫秒）
DURATION_MS = 700
#: 出场（spawn）比退场更长一点：汇聚 + 能量环 + 扫描 + 收尾闪光，更绚丽有科技感
SPAWN_DURATION_MS = 950
FRAME_MS = 33
#: 特效画布边长（覆盖粒子飞散范围，中心对齐宠物）
CANVAS = 360
#: 配色：科技青 + 主题橘
CYAN = QColor(53, 224, 255)
ORANGE = QColor(232, 164, 92)

_PARTICLE_COUNT = 26


def _ease_out(t: float) -> float:
    """ease-out cubic：起步快、收尾缓，粒子汇聚更有冲击感。"""
    return 1.0 - (1.0 - t) ** 3


class TechEffect(QWidget):
    """以 center 为中心的科技感特效窗。mode: "spawn"（汇聚现身）/ "exit"（消散退场）。"""

    finished = Signal()

    def __init__(self, center: QPoint, mode: str = "spawn",
                 rng: random.Random | None = None):
        super().__init__(None)
        self._mode = mode
        self._duration = SPAWN_DURATION_MS if mode == "spawn" else DURATION_MS
        self._center_local = QPoint(CANVAS // 2, CANVAS // 2)
        self._elapsed = 0
        rng = rng or random.Random()
        half = CANVAS // 2
        # 每颗粒子：方向角 / 最大飞行半径 / 尺寸 / 颜色（青橘交替）/ 相位抖动
        self._particles = [
            {
                "angle": rng.uniform(0, math.tau),
                "radius": rng.uniform(half * 0.45, half * 0.95),
                "size": rng.uniform(1.5, 3.5),
                "cyan": index % 2 == 0,
                "jitter": rng.uniform(0.85, 1.15),
            }
            for index in range(_PARTICLE_COUNT)
        ]

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        top_left = QPoint(center.x() - half, center.y() - half)
        self.setGeometry(top_left.x(), top_left.y(), CANVAS, CANVAS)

        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._advance)

    # ---------- 命令 ----------

    def start(self) -> None:
        self.show()
        self.raise_()
        self._timer.start()

    def stop(self) -> None:
        """提前终止（退出/测试清理）：停表、关窗，不再发出 finished。"""
        self._timer.stop()
        self.close()

    # ---------- 内部 ----------

    def _advance(self) -> None:
        self._elapsed += FRAME_MS
        if self._elapsed >= self._duration:
            self._timer.stop()
            self.hide()
            self.finished.emit()
            return
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 重写
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            t = min(1.0, self._elapsed / self._duration)
            if self._mode == "spawn":
                self._paint_spawn(painter, t)
            else:
                self._paint_exit(painter, t)
        finally:
            painter.end()

    # ---------- 两种模式 ----------

    def _paint_spawn(self, painter: QPainter, t: float) -> None:
        """粒子从四周汇聚到中心 + 汇聚能量环 + 旋转扫描环，末段白核闪光爆点（随后宠物淡入）。"""
        ease = _ease_out(t)
        self._draw_converge_rings(painter, ease)
        self._draw_scan_ring(painter, t)
        for particle in self._particles:
            radius = particle["radius"] * (1.0 - ease) * particle["jitter"]
            if radius < 2:
                continue
            angle = particle["angle"]
            dx, dy = math.cos(angle), math.sin(angle)
            x = self._center_local.x() + dx * radius
            y = self._center_local.y() + dy * radius
            color = QColor(CYAN if particle["cyan"] else ORANGE)
            color.setAlpha(int(140 + 115 * t))
            # 拖尾线段：指向中心，越接近中心越短（速度感的"流入"）
            tail = min(22.0, radius * 0.45)
            pen = QPen(color, particle["size"] * (1.0 + 0.4 * t))
            painter.setPen(pen)
            painter.drawLine(QPoint(int(x - dx * tail), int(y - dy * tail)),
                             QPoint(int(x), int(y)))
        if t > 0.7:
            flash = (t - 0.7) / 0.3
            self._draw_glow(painter, int(52 + 30 * math.sin(flash * math.pi)),
                            int(190 * flash))
            self._draw_core_flash(painter, flash)

    def _draw_converge_rings(self, painter: QPainter, ease: float) -> None:
        """两圈能量环由外向内收缩锁定中心，越近越亮（HUD 感）。"""
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for phase in (0.0, 0.3):
            p = min(1.0, ease + phase)
            radius = (CANVAS // 2 - 12) * (1.0 - p)
            if radius < 6:
                continue
            ring = QColor(CYAN)
            ring.setAlpha(int(50 + 150 * p))
            painter.setPen(QPen(ring, 2))
            painter.drawEllipse(self._center_local, int(radius), int(radius))

    def _draw_scan_ring(self, painter: QPainter, t: float) -> None:
        """四段短弧组成的旋转扫描环，像仪表在扫描锁定，纯装饰。"""
        radius = CANVAS // 2 - 28
        painter.save()
        painter.translate(self._center_local)
        painter.rotate(120.0 * t)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(4):
            color = QColor(CYAN if i % 2 == 0 else ORANGE)
            color.setAlpha(int(160 * (1.0 - t * 0.4)))
            pen = QPen(color, 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawArc(-radius, -radius, radius * 2, radius * 2,
                            i * 90 * 16, 42 * 16)
        painter.restore()

    def _draw_core_flash(self, painter: QPainter, flash: float) -> None:
        """收尾白核爆闪：一次正弦脉冲，闪完即隐，宠物正好淡入现身。"""
        alpha = int(230 * math.sin(flash * math.pi))
        if alpha <= 0:
            return
        core = QColor(255, 255, 255)
        core.setAlpha(alpha)
        radius = int(6 + 18 * flash)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(core)
        painter.drawEllipse(self._center_local, radius, radius)

    def _paint_exit(self, painter: QPainter, t: float) -> None:
        """粒子从中心向外爆散，能量环扩张淡出（与宠物淡出同步）。"""
        ease = _ease_out(t)
        for particle in self._particles:
            radius = particle["radius"] * ease * particle["jitter"]
            if radius < 2:
                continue
            angle = particle["angle"]
            dx, dy = math.cos(angle), math.sin(angle)
            x = self._center_local.x() + dx * radius
            y = self._center_local.y() + dy * radius
            color = QColor(CYAN if particle["cyan"] else ORANGE)
            color.setAlpha(int(200 * (1.0 - t)))
            tail = min(20.0, radius * 0.4)
            pen = QPen(color, particle["size"])
            painter.setPen(pen)
            painter.drawLine(QPoint(int(x), int(y)),
                             QPoint(int(x - dx * tail), int(y - dy * tail)))
        # 扩张能量环：半径随 t 变大，透明度随 t 变小
        ring_radius = 20 + ease * (CANVAS // 2 - 30)
        ring = QColor(CYAN)
        ring.setAlpha(int(180 * (1.0 - t)))
        pen = QPen(ring, 2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(self._center_local, int(ring_radius), int(ring_radius))
        if t < 0.4:
            self._draw_glow(painter, 40, int(120 * (1.0 - t / 0.4)))

    def _draw_glow(self, painter: QPainter, radius: int, alpha: int) -> None:
        """中心径向光晕。"""
        if alpha <= 0 or radius <= 0:
            return
        glow = QRadialGradient(self._center_local, radius)
        c1 = QColor(CYAN)
        c1.setAlpha(alpha)
        c2 = QColor(CYAN)
        c2.setAlpha(0)
        glow.setColorAt(0.0, c1)
        glow.setColorAt(1.0, c2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(self._center_local, radius, radius)
