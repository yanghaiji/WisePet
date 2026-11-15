"""宠物登场/退场特效：纯 QPainter 程序化绘制，30fps，无额外素材依赖。

统一骨架（与 FR v1.20 C37 同源）：7 种进入特效**共享**最初那套三段式
「汇聚能量环 + 旋转扫描环 → 各风格主体 → 收尾白核爆闪 → 宠物淡入」，
只在主体段保留风格差异，避免各画各的、节奏与质感割裂。

两条硬约束：
1. **随机参数只在构造时算一次并缓存**，绘制阶段只读。否则碎片落点 / 字符 /
   波纹配色 / 光束高度每帧重新随机，动画会抖，观感廉价（本次整体优化的重点）。
2. **所有进入特效统一 950ms**（与最初的 spawn 一致），并在 82% 处提前发
   `finished`，让宠物 220ms 淡入与白核爆闪重叠，而不是「特效没了才淡入」。

进入特效（主体风格各异）：
  - spawn     ：粒子从四周汇聚到中心
  - burst     ：粒子向外爆开 + 双层冲击波环
  - puzzle    ：渐变三角碎片从四周飞入拼成放射图案
  - terminal  ：HUD 扫描面板自上而下扫出数据网格
  - curtain   ：双向能量分界线扫过并汇合
  - ink       ：同心能量波纹由内向外扩散
  - magnifier ：聚焦光束横向扫过
退场特效 exit 维持「粒子消散」语义（隐藏 / 退出程序时播放）。

`kind` 决定样式；`mode="exit"` 为退场。旧调用 `TechEffect(center, mode="spawn")`
仍兼容（mode 作为 kind 别名）。
"""
from __future__ import annotations

import math
import random

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QLinearGradient, QPainter,
                           QPen, QPolygonF, QRadialGradient)
from PySide6.QtWidgets import QWidget

#: 帧间隔（毫秒）
FRAME_MS = 33
#: 特效画布边长（覆盖绘制范围，中心对齐宠物）
CANVAS = 360
#: 退场时长
DURATION_MS = 700

#: 对外常量：进入特效种类，供设置项校验 / 文档引用
ENTER_KINDS = ("spawn", "burst", "puzzle", "terminal", "curtain", "ink", "magnifier")

#: 统一节奏：所有进入特效 950ms（沿用 FR v1.20 C37 的出场时长），退场仍 700ms
ENTER_DURATION_MS = {kind: 950 for kind in ENTER_KINDS}
EXIT_DURATION_MS = DURATION_MS

#: 统一三段式相位：主体在 0~66% 完成，66%~100% 为收尾白核爆闪
MAIN_END = 0.66
#: 提前发 finished 的位置：宠物 220ms 淡入与收尾爆闪重叠，避免「空白一帧」
REVEAL_AT = 0.82

#: 配色：科技青 + 主题橘（与最初 spawn 同源）
CYAN = QColor(53, 224, 255)
ORANGE = QColor(232, 164, 92)

#: 三角碎片只用这两种亮色，做「发光描边 + 半透明填充」。
#: 不能用暗色实心块——整套特效都是光效，暗块会显得脏且与骨架割裂。
PUZZLE_TONES = (CYAN, ORANGE)

_PARTICLE_COUNT = 32

#: 触发场景 → 进入特效（配置为「自动」时按此分派；toggle 由组装根改为随机）
TRIGGER_ENTER_KIND = {
    "boot": "spawn",       # 开机：粒子汇聚
    "toggle": "ink",       # 托盘恢复（auto 下实际走 random_enter_kind，此项仅作兜底）
    "chat": "terminal",    # 命令面板聊天：HUD 召唤
    "scene": "curtain",    # 切场景：能量分界扫开
    "celebrate": "burst",  # 提醒/番茄：粒子爆开
}


def random_enter_kind(exclude=()) -> str:
    """从进入特效池随机选一种，供「随机」配置模式与 auto 下 toggle 使用。"""
    pool = [k for k in ENTER_KINDS if k not in exclude]
    return random.choice(pool)


def _ease_out(t: float) -> float:
    """ease-out cubic：起步快、收尾缓。"""
    return 1.0 - (1.0 - t) ** 3


def _ease_in_out(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


def _envelope(t: float) -> float:
    """进入特效整体淡入淡出：消除「硬起硬停」，首尾各 6% 渐入渐出。"""
    if t < 0.06:
        return t / 0.06
    if t > 0.94:
        return max(0.0, (1.0 - t) / 0.06)
    return 1.0


def _exit_opacity(t: float) -> float:
    """退场渐隐，与进入的淡出包络对称。"""
    return max(0.0, 1.0 - t * t)


class TechEffect(QWidget):
    """以 center 为中心的特效窗。kind 决定样式，mode=exit 为退场。"""

    finished = Signal()

    def __init__(self, center: QPoint, kind: str = "spawn",
                 mode: str | None = None, rng: random.Random | None = None):
        super().__init__(None)
        self._kind = mode if mode is not None else kind
        if self._kind == "exit":
            self._mode = "exit"
            self._duration = EXIT_DURATION_MS
        else:
            self._mode = "enter"
            self._duration = ENTER_DURATION_MS.get(self._kind, 950)
        self._center_local = QPoint(CANVAS // 2, CANVAS // 2)
        self._elapsed = 0
        self._revealed = False
        rng = rng or random.Random()
        self._rng = rng
        half = CANVAS // 2

        # 粒子：构造时一次生成，绘制阶段只读
        self._particles = [
            {
                "angle": rng.uniform(0, math.tau),
                "radius": rng.uniform(half * 0.4, half * 1.0),
                "size": rng.uniform(1.2, 4.5),
                "cyan": rng.random() < 0.55,
                "jitter": rng.uniform(0.8, 1.2),
                "speed": rng.uniform(0.8, 1.25),
            }
            for _ in range(_PARTICLE_COUNT + rng.randint(-4, 6))
        ]

        # ---- 各特效的随机参数：全部一次性生成并缓存，绘制阶段绝不重新随机 ----
        self._rot_offset = rng.uniform(0, math.tau)
        self._burst_ring_delay = rng.uniform(0.05, 0.2)

        # puzzle：每个碎片的目标点 / 散落起点 / 旋转差 / 配色定死
        self._puzzle_size = rng.uniform(24, 34)
        piece_half = CANVAS / 2 - 20
        piece_count = rng.randint(14, 22)
        self._puzzle_pieces = []
        for i in range(piece_count):
            target_angle = (i / piece_count) * math.tau - math.pi / 2 \
                + rng.uniform(-0.1, 0.1)
            scatter_r = CANVAS * rng.uniform(0.55, 0.85)
            scatter_ang = rng.uniform(0, math.tau)
            self._puzzle_pieces.append({
                "tx": self._center_local.x()
                      + math.cos(target_angle) * piece_half * rng.uniform(0.28, 0.45),
                "ty": self._center_local.y()
                      + math.sin(target_angle) * piece_half * rng.uniform(0.28, 0.45),
                "sx": self._center_local.x() + math.cos(scatter_ang) * scatter_r,
                "sy": self._center_local.y() + math.sin(scatter_ang) * scatter_r,
                "rot": scatter_ang - target_angle,
                "cyan": rng.random() < 0.55,
            })

        # terminal：字符网格一次生成，避免每帧字符乱跳
        self._terminal_font = rng.choice([10, 11, 12])
        self._terminal_cols = rng.randint(7, 13)
        self._terminal_rows = self._terminal_cols + 2
        self._terminal_grid = [
            [rng.choice("01111100OIDAEU") for _ in range(self._terminal_cols)]
            for _ in range(self._terminal_rows)
        ]

        # ink：每道波纹的配色一次定死，避免每帧颜色闪
        self._ink_waves = rng.randint(4, 7)
        self._ink_gap = rng.uniform(0.14, 0.24)
        self._ink_colors = [
            (i % 2 == 0) ^ (rng.random() < 0.3) for i in range(self._ink_waves)
        ]

        # magnifier：光束高度与拖尾系数一次定死，避免每帧抖
        self._magnifier_r = rng.uniform(68, 90)
        self._magnifier_offset = rng.uniform(-30, 30)
        self._magnifier_cy = float(self._center_local.y() + rng.uniform(-15, 15))
        self._magnifier_tail_factor = rng.uniform(0.7, 1.3)

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

    # ---------- 驱动 ----------

    def _advance(self) -> None:
        self._elapsed += FRAME_MS
        # 进入特效在 82% 处提前通知组装根：宠物淡入与收尾爆闪重叠
        if (self._mode == "enter" and not self._revealed
                and self._elapsed >= self._duration * REVEAL_AT):
            self._revealed = True
            self.finished.emit()
        if self._elapsed >= self._duration:
            self._timer.stop()
            self.hide()
            if not self._revealed:          # 退场 / 兜底：结束也必发一次
                self._revealed = True
                self.finished.emit()
            return
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 重写
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            t = min(1.0, self._elapsed / self._duration)
            if self._mode == "enter":
                painter.setOpacity(_envelope(t))
                self._paint_enter(painter, t, self._kind)
            else:
                painter.setOpacity(_exit_opacity(t))
                self._paint_exit(painter, t)
        finally:
            painter.end()

    # ---------- 统一三段式骨架 ----------

    def _paint_enter(self, painter: QPainter, t: float, kind: str) -> None:
        """所有进入特效共享 C37 骨架：铺垫 → 各风格主体 → 收尾爆闪。"""
        main_t = min(1.0, t / MAIN_END)
        ease = _ease_out(main_t)
        self._draw_converge_rings(painter, ease)
        self._draw_scan_ring(painter, t)

        dispatch = {
            "spawn": self._paint_spawn,
            "burst": self._paint_burst,
            "puzzle": self._paint_puzzle,
            "terminal": self._paint_terminal,
            "curtain": self._paint_curtain,
            "ink": self._paint_ink,
            "magnifier": self._paint_magnifier,
        }
        dispatch.get(kind, self._paint_spawn)(painter, main_t)

        self._draw_finale(painter, t)

    def _draw_finale(self, painter: QPainter, t: float) -> None:
        """统一收尾：光晕 + 白核爆闪 + 十字准星（与最初 spawn 完全一致）。"""
        if t <= MAIN_END:
            return
        flash = (t - MAIN_END) / (1.0 - MAIN_END)
        self._draw_glow(painter, int(55 + 35 * flash), int(200 * flash))
        self._draw_core_flash(painter, flash)
        if flash > 0.3:
            self._draw_crosshair(painter, int(14 + 10 * flash),
                                 int(150 * (1.0 - flash)))

    # ---------- 主体：粒子汇聚（开机） ----------

    def _paint_spawn(self, painter: QPainter, t: float) -> None:
        ease = _ease_out(t)
        for particle in self._particles:
            radius = particle["radius"] * (1.0 - ease) * particle["jitter"]
            if radius < 2:
                continue
            angle = particle["angle"] + self._rot_offset
            dx, dy = math.cos(angle), math.sin(angle)
            x = self._center_local.x() + dx * radius
            y = self._center_local.y() + dy * radius
            base = CYAN if particle["cyan"] else ORANGE
            color = QColor(base)
            color.setAlpha(int(120 + 135 * t))
            tail = min(28.0, radius * 0.55)
            painter.setPen(QPen(color, particle["size"] * (1.0 + 0.5 * t)))
            painter.drawLine(QPoint(int(x - dx * tail), int(y - dy * tail)),
                             QPoint(int(x), int(y)))
            head = QColor(base)
            head.setAlpha(int(200 * t))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(head)
            painter.drawEllipse(QPointF(x, y), particle["size"] * 0.8,
                                particle["size"] * 0.8)

    # ---------- 主体：粒子爆开（庆祝） ----------

    def _paint_burst(self, painter: QPainter, t: float) -> None:
        ease = _ease_out(t)
        for particle in self._particles:
            radius = particle["radius"] * ease * particle["jitter"] * particle["speed"]
            if radius < 2:
                continue
            dx = math.cos(particle["angle"])
            dy = math.sin(particle["angle"])
            x = self._center_local.x() + dx * radius
            y = self._center_local.y() + dy * radius
            base = CYAN if particle["cyan"] else ORANGE
            color = QColor(base)
            color.setAlpha(int(240 * (1.0 - t)))
            tail = min(32.0, radius * 0.5)
            painter.setPen(QPen(color, particle["size"] * 1.2))
            painter.drawLine(QPoint(int(x), int(y)),
                             QPoint(int(x - dx * tail), int(y - dy * tail)))
            head = QColor(base)
            head.setAlpha(int(220 * (1.0 - t)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(head)
            painter.drawEllipse(QPointF(x, y), particle["size"], particle["size"])
        self._draw_burst_rings(painter, ease, t)

    # ---------- 主体：三角碎片拼合（几何） ----------

    def _paint_puzzle(self, painter: QPainter, t: float) -> None:
        """碎片落点/配色读构造期缓存，同一 t 连画两次像素完全一致。"""
        assemble = _ease_out(t)
        size = self._puzzle_size
        for piece in self._puzzle_pieces:
            cur_x = piece["sx"] + (piece["tx"] - piece["sx"]) * assemble
            cur_y = piece["sy"] + (piece["ty"] - piece["sy"]) * assemble
            alpha = int(220 * (1.0 - max(0.0, t - 0.88) / 0.12))
            if alpha <= 0:
                continue
            tone = PUZZLE_TONES[0 if piece["cyan"] else 1]
            # 发光描边用满 alpha；填充从半透明亮色渐隐到全透明，
            # 与粒子/光晕的「光效」语言一致，而不是不透明色块。
            edge = QColor(tone)
            edge.setAlpha(alpha)
            head = QColor(tone)
            head.setAlpha(int(alpha * 0.42))
            tail = QColor(tone)
            tail.setAlpha(0)
            painter.save()
            painter.translate(QPointF(cur_x, cur_y))
            painter.rotate(math.degrees(piece["rot"] * (1.0 - assemble)))
            tri = QLinearGradient(0, -size, 0, size)
            tri.setColorAt(0.0, head)
            tri.setColorAt(1.0, tail)
            painter.setBrush(QBrush(tri))
            painter.setPen(QPen(edge, 1.5))
            painter.drawPolygon(QPolygonF([
                QPointF(0, -size),
                QPointF(size * 0.866, size * 0.5),
                QPointF(-size * 0.866, size * 0.5),
            ]))
            painter.restore()

    # ---------- 主体：HUD 扫描（极客） ----------

    def _paint_terminal(self, painter: QPainter, t: float) -> None:
        pad = 32
        rect = QRectF(pad, pad, CANVAS - 2 * pad, CANVAS - 2 * pad)
        content_top = rect.top() + 12
        content_bottom = rect.bottom() - 12
        scan_y = content_top + (content_bottom - content_top) * _ease_out(t)
        frame_fade = 1.0 - t * 0.3

        painter.setBrush(Qt.BrushStyle.NoBrush)
        outer = QColor(CYAN)
        outer.setAlpha(int(140 * frame_fade))
        painter.setPen(QPen(outer, 2))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
        inner = QColor(ORANGE)
        inner.setAlpha(int(110 * frame_fade))
        painter.setPen(QPen(inner, 1))
        painter.drawRect(rect.adjusted(6, 6, -6, -6))
        self._draw_corner_marks(painter, rect, 14)

        font = painter.font()
        font.setPixelSize(self._terminal_font)
        painter.setFont(font)
        cols = self._terminal_cols
        rows = self._terminal_rows
        cell_w = (rect.width() - 16) / cols
        cell_h = (content_bottom - content_top) / rows
        for c in range(cols):
            for r in range(rows):
                yy = content_top + r * cell_h + cell_h / 2
                if yy > scan_y + 4:
                    continue
                distance = scan_y - yy
                brightness = (max(0.4, 1.0 - distance / (cell_h * 3))
                              if distance < cell_h * 2 else 0.65)
                col = QColor(CYAN)
                col.setAlpha(int(200 * brightness * frame_fade))
                painter.setPen(col)
                painter.drawText(QPointF(rect.left() + 12 + c * cell_w, yy),
                                 self._terminal_grid[r][c])

        if scan_y > content_top:
            glow_h = 14
            glow = QLinearGradient(0, scan_y - glow_h, 0, scan_y + glow_h)
            glow.setColorAt(0.0, QColor(232, 164, 92, 0))
            glow.setColorAt(0.5, QColor(232, 164, 92, 210))
            glow.setColorAt(1.0, QColor(232, 164, 92, 0))
            painter.setBrush(QBrush(glow))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRect(QRectF(rect.left() + 4, scan_y - glow_h,
                                    rect.width() - 8, glow_h * 2))
        painter.setPen(QPen(ORANGE, 2))
        painter.drawLine(QPointF(rect.left() + 4, scan_y),
                         QPointF(rect.right() - 4, scan_y))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ORANGE)
        for edge_x in (rect.left() + 4, rect.right() - 4):
            painter.drawEllipse(QPointF(edge_x, scan_y), 4, 4)
        painter.setPen(QPen(ORANGE, 1.5))
        for edge_x in (rect.left() + 4, rect.right() - 4):
            painter.drawLine(QPointF(edge_x, scan_y - 6),
                             QPointF(edge_x, scan_y + 6))
        self._draw_hud_status(painter, rect)

    def _draw_corner_marks(self, painter: QPainter, rect: QRectF, size: float) -> None:
        col = QColor(CYAN)
        col.setAlpha(170)
        painter.setPen(QPen(col, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        left, right = rect.left(), rect.right()
        top, bottom = rect.top(), rect.bottom()
        for cx, cy, dx, dy in ((left, top, 1, 1), (right, top, -1, 1),
                               (left, bottom, 1, -1), (right, bottom, -1, -1)):
            painter.drawLine(QPointF(cx, cy + dy * size), QPointF(cx, cy))
            painter.drawLine(QPointF(cx, cy), QPointF(cx + dx * size, cy))

    def _draw_hud_status(self, painter: QPainter, rect: QRectF) -> None:
        sx, sy = rect.right() - 22, rect.bottom() - 14
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ORANGE)
        painter.drawEllipse(QPointF(sx, sy), 3, 3)
        painter.setPen(QPen(QColor(CYAN), 1.5))
        painter.drawLine(QPointF(sx + 6, sy), QPointF(sx + 18, sy))

    # ---------- 主体：双向能量分界扫开（揭幕） ----------

    def _paint_curtain(self, painter: QPainter, t: float) -> None:
        k = CANVAS * (1.0 - 2.0 * _ease_out(t))
        self._draw_dual_boundaries(painter, k, t)

    def _draw_dual_boundaries(self, painter: QPainter, k: float, t: float) -> None:
        thick = 12
        col = QLinearGradient(0, -thick, 0, thick)
        c_outer = QColor(ORANGE)
        c_outer.setAlpha(0)
        c_mid = QColor(ORANGE)
        c_mid.setAlpha(210)
        col.setColorAt(0.0, c_outer)
        col.setColorAt(0.45, c_mid)
        col.setColorAt(0.55, c_mid)
        col.setColorAt(1.0, c_outer)

        def _line(a: QPointF, b: QPointF) -> None:
            painter.setPen(QPen(col, thick * 2))
            painter.drawLine(a, b)
            painter.setPen(QPen(CYAN, 2))
            painter.drawLine(a, b)

        if k >= 0:
            _line(QPointF(k, 0), QPointF(CANVAS, CANVAS - k))
            _line(QPointF(0, CANVAS - k), QPointF(CANVAS - k, CANVAS))
        else:
            mk = -k
            _line(QPointF(0, mk), QPointF(mk, 0))
            _line(QPointF(CANVAS - mk, CANVAS), QPointF(CANVAS, CANVAS - mk))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(CYAN)
        endpoints = ((QPointF(k, 0), QPointF(CANVAS, CANVAS - k),
                      QPointF(0, CANVAS - k), QPointF(CANVAS - k, CANVAS))
                     if k >= 0 else
                     (QPointF(0, -k), QPointF(-k, 0),
                      QPointF(CANVAS + k, CANVAS), QPointF(CANVAS, CANVAS + k)))
        for point in endpoints:
            painter.drawEllipse(point, 4, 4)

        if k >= 0 and 1.0 - k / CANVAS > 0.2:
            for i in range(5):
                phase = t * 5 - i * 0.18
                if phase < 0 or phase > 1.0:
                    continue
                rx = CANVAS / 2 + (CANVAS / 2 - 14) * _ease_out(phase)
                ripple_r = 4 + 22 * phase
                rc = QColor(CYAN if i % 2 == 0 else ORANGE)
                rc.setAlpha(int(160 * (1.0 - phase)))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(rc, 2))
                painter.drawEllipse(QPointF(rx, CANVAS / 2),
                                    int(ripple_r), int(ripple_r))

    # ---------- 主体：同心能量波纹（优雅） ----------

    def _paint_ink(self, painter: QPainter, t: float) -> None:
        for i in range(self._ink_waves):
            phase = t * self._ink_waves - i * self._ink_gap
            if phase < 0 or phase > 1.15:
                continue
            radius = (CANVAS / 2 - 24) * _ease_out(min(1.0, phase))
            if radius < 3:
                continue
            base = CYAN if self._ink_colors[i] else ORANGE
            alpha = int(180 * (1.0 - phase) * (1.0 - max(0, phase - 1.0) * 5))
            if alpha <= 0:
                continue
            ring_width = max(12, 28 - int(phase * 16))
            glow = QRadialGradient(self._center_local, radius + ring_width)
            c_edge, c_inner = QColor(base), QColor(base)
            c_edge.setAlpha(0)
            c_mid = QColor(base)
            c_mid.setAlpha(alpha)
            c_inner.setAlpha(0)
            glow.setColorAt(max(0.0, (radius - ring_width) / (radius + ring_width)),
                            c_inner)
            glow.setColorAt(radius / (radius + ring_width), c_mid)
            glow.setColorAt(1.0, c_edge)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(glow))
            painter.drawEllipse(self._center_local,
                                int(radius + ring_width), int(radius + ring_width))
            pen_col = QColor(base)
            pen_col.setAlpha(int(min(220, alpha + 40)))
            painter.setPen(QPen(pen_col, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(self._center_local, int(radius), int(radius))

        center_alpha = int(220 * (1.0 - min(1.0, t * 1.5)))
        if center_alpha > 0:
            self._draw_glow(painter, 50, center_alpha)
            center_col = QColor(CYAN)
            center_col.setAlpha(center_alpha)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(center_col)
            painter.drawEllipse(self._center_local, 5, 5)

    # ---------- 主体：聚焦光束扫描（寻回） ----------

    def _paint_magnifier(self, painter: QPainter, t: float) -> None:
        cx = (-50 + (CANVAS + 100) * t
              + self._magnifier_offset * (1 - abs(t - 0.5) * 1.5))
        cy = self._magnifier_cy
        R = self._magnifier_r
        tail_len = 60 * t * self._magnifier_tail_factor
        intensity = 1.0 - abs(t - 0.5) * 1.2

        if tail_len > 5:
            tail_col = QColor(ORANGE)
            tail_col.setAlpha(int(90 * intensity))
            tail_end = QColor(ORANGE)
            tail_end.setAlpha(0)
            tail = QLinearGradient(cx - tail_len, cy, cx, cy)
            tail.setColorAt(0.0, tail_end)
            tail.setColorAt(1.0, tail_col)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(tail))
            painter.drawPolygon(QPolygonF([
                QPointF(cx - tail_len, cy - R * 0.3),
                QPointF(cx, cy - R * 0.75),
                QPointF(cx, cy + R * 0.75),
                QPointF(cx - tail_len, cy + R * 0.3),
            ]))

        glow = QRadialGradient(cx, cy, R)
        c_core = QColor(200, 250, 255, int(220 * intensity))
        c_mid = QColor(CYAN)
        c_mid.setAlpha(int(120 * intensity))
        c_edge = QColor(CYAN)
        c_edge.setAlpha(0)
        glow.setColorAt(0.0, c_core)
        glow.setColorAt(0.5, c_mid)
        glow.setColorAt(1.0, c_edge)
        painter.setBrush(QBrush(glow))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(cx, cy), R, R)

        ring_col = QColor(ORANGE)
        ring_col.setAlpha(int(180 * intensity))
        painter.setPen(QPen(ring_col, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(cx, cy), int(R * 0.95), int(R * 0.95))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, int(200 * intensity)))
        painter.drawEllipse(QPointF(cx, cy), 4, 4)

    # ---------- 退场特效 ----------

    def _paint_exit(self, painter: QPainter, t: float) -> None:
        ease = _ease_out(t)
        for particle in self._particles:
            radius = particle["radius"] * ease * particle["jitter"] * particle["speed"]
            if radius < 2:
                continue
            dx = math.cos(particle["angle"])
            dy = math.sin(particle["angle"])
            x = self._center_local.x() + dx * radius
            y = self._center_local.y() + dy * radius
            base = CYAN if particle["cyan"] else ORANGE
            color = QColor(base)
            color.setAlpha(int(210 * (1.0 - t)))
            tail = min(24.0, radius * 0.45)
            painter.setPen(QPen(color, particle["size"]))
            painter.drawLine(QPoint(int(x), int(y)),
                             QPoint(int(x - dx * tail), int(y - dy * tail)))
        self._draw_burst_rings(painter, ease, t)

    # ---------- 公共绘制件（C37 骨架） ----------

    def _draw_converge_rings(self, painter: QPainter, ease: float) -> None:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for phase in (0.0, 0.3):
            p = min(1.0, ease + phase)
            radius = (CANVAS // 2 - 12) * (1.0 - p)
            if radius < 6:
                continue
            base = CYAN if phase == 0.0 else ORANGE
            ring = QColor(base)
            ring.setAlpha(int(50 + 160 * p))
            painter.setPen(QPen(ring, 2))
            painter.drawEllipse(self._center_local, int(radius), int(radius))

    def _draw_scan_ring(self, painter: QPainter, t: float) -> None:
        radius = CANVAS // 2 - 28
        painter.save()
        painter.translate(self._center_local)
        painter.rotate(120.0 * t + math.degrees(self._rot_offset))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(4):
            base = CYAN if i % 2 == 0 else ORANGE
            color = QColor(base)
            color.setAlpha(int(160 * (1.0 - t * 0.4)))
            pen = QPen(color, 2.5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawArc(-radius, -radius, radius * 2, radius * 2,
                            i * 90 * 16, 45 * 16)
        painter.restore()

    def _draw_core_flash(self, painter: QPainter, flash: float) -> None:
        alpha = int(240 * math.sin(flash * math.pi))
        if alpha <= 0:
            return
        core = QColor(255, 255, 255)
        core.setAlpha(alpha)
        radius = int(6 + 20 * flash)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(core)
        painter.drawEllipse(self._center_local, radius, radius)

    def _draw_glow(self, painter: QPainter, radius: int, alpha: int) -> None:
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

    def _draw_burst_rings(self, painter: QPainter, ease: float, t: float) -> None:
        half = CANVAS // 2
        delay = self._burst_ring_delay
        outer_ease = max(0.0, ease - delay) / (1.0 - delay) if delay < 1.0 else 0.0
        outer_col = QColor(CYAN)
        outer_col.setAlpha(int(210 * (1.0 - t)))
        painter.setPen(QPen(outer_col, 2.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(self._center_local,
                            int(14 + outer_ease * (half - 18)),
                            int(14 + outer_ease * (half - 18)))
        inner_col = QColor(ORANGE)
        inner_col.setAlpha(int(170 * (1.0 - t)))
        painter.setPen(QPen(inner_col, 1.5))
        painter.drawEllipse(self._center_local,
                            int(10 + ease * 0.7 * (half - 28)),
                            int(10 + ease * 0.7 * (half - 28)))

    def _draw_crosshair(self, painter: QPainter, size: int, alpha: int) -> None:
        if alpha <= 0 or size <= 2:
            return
        col = QColor(CYAN)
        col.setAlpha(alpha)
        painter.setPen(QPen(col, 1.5))
        cx, cy = self._center_local.x(), self._center_local.y()
        painter.drawLine(QPoint(cx - size, cy), QPoint(cx - 3, cy))
        painter.drawLine(QPoint(cx + 3, cy), QPoint(cx + size, cy))
        painter.drawLine(QPoint(cx, cy - size), QPoint(cx, cy - 3))
        painter.drawLine(QPoint(cx, cy + 3), QPoint(cx, cy + size))
        corner = size - 2
        for dx, dy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
            painter.drawLine(QPoint(cx + dx * corner, cy + dy * (corner - 4)),
                             QPoint(cx + dx * corner, cy + dy * corner))
            painter.drawLine(QPoint(cx + dx * (corner - 4), cy + dy * corner),
                             QPoint(cx + dx * corner, cy + dy * corner))
