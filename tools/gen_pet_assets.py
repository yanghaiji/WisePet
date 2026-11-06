"""桌宠序列帧离线生成器（Q 版可爱风 / chibi）。

用法：
    python tools/gen_pet_assets.py                       # 只重生默认皮肤 cat（10 动作 × 6 帧）
    python tools/gen_pet_assets.py --skin all            # 三皮肤全套
    python tools/gen_pet_assets.py --skin cat --preview  # 另出 tmp/preview-cat.png 目检
    python tools/gen_pet_assets.py --action walk,run     # 单动作重生（目检迭代不必重跑 60 帧）

动作清单取自 `WisePet.domain.actions.ACTION_NAMES`（PRD v2.0 C12 单一真相源），
本脚本不得另写一份，否则会出现"素材生成了但运行时读不到"的静默漂移。

设计取向（PRD v1.3 §1，用户真机反馈“写实厚涂在小尺寸下诡异、不可爱”）：
- 大头小身（头高≈108px 而身长≈82px，头比身子还大）、眼睛大而低位、圆瞳双高光，去掉写实眼裂/竖瞳/limbal ring
- 干净的同色系深描边（不是黑边），单层体积明暗，不做逐根毛发
- 毛边用“软凸起”（圆头短线藏在剪影后面）而不是炸开的毛刺，且只在脸侧留两簇
- 腮红 + 迷你鼻 + ω 小嘴；胡须极淡，每侧两根短而下坠的软线

本脚本允许 import PySide6（AGENT.md §2.3 登记的例外），但不得被 src/WisePet/** 与 main.py 导入。
笔触随机数以 `皮肤|动作` 为种子（不带帧号：同一动作的六帧长在同一撮毛上），
同参数重跑逐字节一致，素材 diff 才有意义。
"""
from __future__ import annotations

import argparse
import math
import random
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from WisePet.domain.actions import (  # noqa: E402
    ACTION_NAMES,
    FRAMES_PER_ACTION,
    FRAME_FOOT_Y,
    FRAME_SOURCE_SIZE,
)

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient

SIZE = FRAME_SOURCE_SIZE
CX = SIZE / 2.0
#: 全动作统一 6 帧：观感差异只由 `domain.actions.FPS_BY_ACTION` 的逐动作帧率表控制，
#: 帧数参差会让素材校验与加载逻辑复杂化（PRD v2.0 C11）。
FRAMES = FRAMES_PER_ACTION
#: 地面线：接触阴影与身体底部都以此为准（与运行时缩放、导入脚本共用 `domain.actions` 的脚线）
BASE_Y = float(FRAME_FOOT_Y)
ACTIONS = ACTION_NAMES
#: 贴纸风外描边宽度（256px 画布）：粗边是"贴纸"读感的第一来源（PRD v2.0 C9）
OUTLINE_W = 6.2
#: 耳朵比头身略细：它们躲在头后，同宽会把头体压小
EAR_OUTLINE_W = 5.0
#: 内部分区线：同色系压深而不是纯黑，小尺寸下才不像线稿
INNER_W = 2.6


def _c(value: str | QColor) -> QColor:
    return value if isinstance(value, QColor) else QColor(value)


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    """两色线性混合，t=0 取 a、t=1 取 b。"""
    return QColor(
        int(a.red() + (b.red() - a.red()) * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue() + (b.blue() - a.blue()) * t),
    )


def _alpha(color: QColor, alpha: int) -> QColor:
    out = QColor(color)
    out.setAlpha(alpha)
    return out


@dataclass(frozen=True)
class Palette:
    """一只 Q 版动物的全部色层（低饱和奶油系，避免高对比显脏）。"""

    base: QColor        # 主毛色
    light: QColor       # 受光面
    shade: QColor       # 暗面 / 条纹
    outline: QColor     # 描边（同色系压深，不用纯黑）
    belly: QColor       # 肚皮 / 口吻 / 爪垫
    blush: QColor       # 腮红
    eye: QColor         # 瞳孔底色
    iris: QColor        # 虹膜（靠中心的暖色）
    nose: QColor
    ear_inner: QColor
    whisker: QColor
    stripe: QColor


@dataclass(frozen=True)
class Chibi:
    """Q 版解剖参数：新增皮肤只加一行参数，不新增绘制代码。"""

    head_r: float           # 头半径（大头）
    head_cy: float          # 头心 y
    body_w: float
    body_h: float
    body_cy: float
    ear: str                # tri | round
    ear_w: float
    ear_h: float
    ear_dx: float           # 相对头心的水平距离
    ear_dy: float           # 相对头心的垂直距离（负=在上）
    eye_r: float
    eye_dx: float
    eye_dy: float           # 相对头心：越大越"低位"，越幼态
    nose_w: float
    tail: str               # bushy | curl | thin
    stripes: bool
    cheek_tufts: bool       # 颊部毛簇
    muzzle_patch: bool      # 口吻浅色块
    fluff: float            # 软毛凸起长度（0=光滑）
    body_mark: str          # bib=胸口浅色块 | none


CAT_PAL = Palette(
    base=_c("#F3B76B"), light=_c("#FBDCAE"), shade=_c("#D98F3E"),
    outline=_c("#8C5A2B"), belly=_c("#FDEBD2"), blush=_c("#F08C8C"),
    eye=_c("#3B2B26"), iris=_c("#C9852F"), nose=_c("#E58C8C"),
    ear_inner=_c("#F2A9A0"), whisker=_c("#FFFFFF"), stripe=_c("#D98F3E"),
)
CAT = Chibi(head_r=56.0, head_cy=96.0, body_w=92.0, body_h=82.0, body_cy=168.0,
            ear="tri", ear_w=34.0, ear_h=34.0, ear_dx=36.0, ear_dy=-40.0,
            eye_r=17.0, eye_dx=21.0, eye_dy=6.0, nose_w=9.0,
            tail="bushy", stripes=True, cheek_tufts=True, muzzle_patch=True,
            fluff=6.0, body_mark="bib")

SHIBA_PAL = Palette(
    base=_c("#F3D6A6"), light=_c("#FCF0DC"), shade=_c("#DDB37B"),
    outline=_c("#A5764A"), belly=_c("#FFFBF3"), blush=_c("#EE8A86"),
    eye=_c("#3A2A24"), iris=_c("#B87A32"), nose=_c("#4C4038"),
    ear_inner=_c("#F6C9B8"), whisker=_c("#FFFFFF"), stripe=_c("#D9AE72"),
)
SHIBA = Chibi(head_r=54.0, head_cy=98.0, body_w=94.0, body_h=84.0, body_cy=170.0,
              ear="tri", ear_w=40.0, ear_h=30.0, ear_dx=34.0, ear_dy=-38.0,
              eye_r=16.0, eye_dx=20.0, eye_dy=7.0, nose_w=11.0,
              tail="curl", stripes=False, cheek_tufts=True, muzzle_patch=True,
              fluff=8.0, body_mark="bib")

MONKEY_PAL = Palette(
    base=_c("#C79A74"), light=_c("#E4C6A8"), shade=_c("#A2764F"),
    outline=_c("#6E4C31"), belly=_c("#F3DCC4"), blush=_c("#E58C8C"),
    eye=_c("#382720"), iris=_c("#9C6B3C"), nose=_c("#8A6045"),
    ear_inner=_c("#E9BFA1"), whisker=_c("#D8BFA6"), stripe=_c("#A2764F"),
)
MONKEY = Chibi(head_r=56.0, head_cy=96.0, body_w=86.0, body_h=78.0, body_cy=170.0,
               ear="round", ear_w=22.0, ear_h=22.0, ear_dx=54.0, ear_dy=-4.0,
               eye_r=17.0, eye_dx=20.0, eye_dy=5.0, nose_w=8.0,
               tail="thin", stripes=False, cheek_tufts=False, muzzle_patch=True,
               fluff=5.0, body_mark="none")

SKINS: dict[str, tuple[Palette, Chibi]] = {
    "cat": (CAT_PAL, CAT),
    "dog": (SHIBA_PAL, SHIBA),
    "monkey": (MONKEY_PAL, MONKEY),
}


# --------------------------------------------------------------------------
# 基础绘制工具
# --------------------------------------------------------------------------

def _pen(color: QColor, width: float) -> QPen:
    """圆头圆角描边：QPen 的位置参数第 3/4 项是 cap/join，显式 setter 更不容易写错。"""
    pen = QPen(color, width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def _ellipse_path(cx: float, cy: float, rx: float, ry: float) -> QPainterPath:
    path = QPainterPath()
    path.addEllipse(QPointF(cx, cy), rx, ry)
    return path


def _fill(p: QPainter, path: QPainterPath, color: QColor,
          pen: QPen | None = None) -> None:
    p.setPen(pen if pen is not None else Qt.NoPen)
    p.setBrush(color)
    p.drawPath(path)


def _stroke(p: QPainter, path: QPainterPath, color: QColor, width: float) -> None:
    """只描边不填（已做过体积渐变的剪体不能再叠一层填充）。"""
    p.setPen(_pen(color, width))
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)


def _body_path(chi: Chibi, squash: float = 1.0) -> QPainterPath:
    """底部更宽的圆润梨形（Q 版身体不画腿，靠两只前爪交代落地）。"""
    w = chi.body_w * squash
    h = chi.body_h / squash
    top = chi.body_cy - h / 2.0
    bot = chi.body_cy + h / 2.0
    path = QPainterPath()
    path.moveTo(CX - w * 0.36, top)
    path.cubicTo(CX - w * 0.56, top + h * 0.34, CX - w * 0.50, bot, CX - w * 0.24, bot)
    path.cubicTo(CX - w * 0.08, bot + h * 0.05, CX + w * 0.08, bot + h * 0.05,
                 CX + w * 0.24, bot)
    path.cubicTo(CX + w * 0.50, bot, CX + w * 0.56, top + h * 0.34, CX + w * 0.36, top)
    path.cubicTo(CX + w * 0.14, top - h * 0.12, CX - w * 0.14, top - h * 0.12,
                 CX - w * 0.36, top)
    path.closeSubpath()
    return path


def _volume(p: QPainter, path: QPainterPath, pal: Palette,
            light_at: tuple[float, float]) -> None:
    """体积感两层就够：上亮下暗的竖向渐变 + 左上高光；描边负责剪影干净。

    竖向而不是对角：光从头顶来、阴影落在地上，读起来才是"一个会呼吸的圆"，
    对角渐变在小尺寸下像被人从右上角擦了一块（PRD v2.0 C9）。
    """
    rect = path.boundingRect()
    p.save()
    p.setClipPath(path)
    p.setPen(Qt.NoPen)
    grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    grad.setColorAt(0.0, pal.light)
    grad.setColorAt(0.52, pal.base)
    grad.setColorAt(1.0, pal.shade)
    p.setBrush(grad)
    p.drawPath(path)
    glow = QRadialGradient(QPointF(*light_at), rect.width() * 0.55)
    glow.setColorAt(0.0, _alpha(pal.light, 165))
    glow.setColorAt(1.0, _alpha(pal.light, 0))
    p.setBrush(glow)
    p.drawPath(path)
    p.restore()


def _soft_patch(p: QPainter, cx: float, cy: float, rx: float, ry: float,
                color: QColor, core_alpha: int, edge: float = 0.68) -> None:
    """软边缘色块（腹部/口吻）：径向渐变到透明，替掉硬边椭圆。"""
    grad = QRadialGradient(QPointF(cx, cy), max(rx, ry))
    grad.setColorAt(0.0, _alpha(color, core_alpha))
    grad.setColorAt(edge, _alpha(color, int(core_alpha * 0.86)))
    grad.setColorAt(1.0, _alpha(color, 0))
    p.setPen(Qt.NoPen)
    p.setBrush(grad)
    p.drawEllipse(QPointF(cx, cy), rx, ry)


def _fluff_behind(p: QPainter, path: QPainterPath, color: QColor,
                  size: float, rng: random.Random, count: int) -> None:
    """软毛边：藏在剪影后面的圆头短线，读感是"绒"而不是"刺"。

    两个必须：① 沿真实剪影取点（`pointAtPercent`），用外接椭圆参数角会让毛飘在身体外；
    ② 法线朝下的段落跳过，否则下巴/肚子长出朝地的硬刺。
    """
    if size <= 0:
        return
    p.save()
    p.setPen(_pen(color, size * 0.85))
    p.setBrush(Qt.NoBrush)
    for i in range(count):
        t = (i + rng.random() * 0.6) / count
        pt = path.pointAtPercent(t)
        ang = math.radians(path.angleAtPercent(t))
        nx, ny = -math.sin(ang), math.cos(ang)
        if ny > 0.55:
            continue
        tip = QPointF(pt.x() + nx * size, pt.y() + ny * size)
        p.drawLine(pt, tip)
    p.restore()


def _contact_shadow(p: QPainter, cy: float, rx: float) -> None:
    """接触阴影：让宠物"坐在地面上"而不是贴纸。"""
    grad = QRadialGradient(QPointF(CX, cy), rx)
    grad.setColorAt(0.0, _alpha(_c("#3A2A24"), 46))
    grad.setColorAt(1.0, _alpha(_c("#3A2A24"), 0))
    p.setPen(Qt.NoPen)
    p.setBrush(grad)
    p.drawEllipse(QPointF(CX, cy), rx, rx * 0.22)


# --------------------------------------------------------------------------
# 部件
# --------------------------------------------------------------------------

def _draw_ear(p: QPainter, pal: Palette, chi: Chibi, side: int,
              head_cx: float, head_cy: float, droop: float) -> None:
    """side=-1 左、1 右；droop>0 让耳尖外扩下垂（难过/受伤）。"""
    bx = head_cx + side * chi.ear_dx
    by = head_cy + chi.ear_dy
    if chi.ear == "round":                      # 猴：头侧圆耳
        path = _ellipse_path(bx + side * 6.0, by, chi.ear_w / 2.0, chi.ear_h / 2.0)
        _fill(p, path, pal.base, _pen(pal.outline, EAR_OUTLINE_W))
        inner = _ellipse_path(bx + side * 7.0, by, chi.ear_w / 3.4, chi.ear_h / 3.4)
        _fill(p, inner, pal.ear_inner)
        return
    tip_x = bx + side * (chi.ear_w * 0.34 + droop * 9.0)
    tip_y = by - chi.ear_h * (1.0 - droop * 0.22)
    path = QPainterPath()
    path.moveTo(bx - side * chi.ear_w * 0.5, by + 8.0)
    path.lineTo(tip_x, tip_y)
    path.lineTo(bx + side * chi.ear_w * 0.5, by + 6.0)
    path.closeSubpath()
    _fill(p, path, pal.base, _pen(pal.outline, EAR_OUTLINE_W))
    inner = QPainterPath()
    inner.moveTo(bx - side * chi.ear_w * 0.22, by + 4.0)
    inner.lineTo(tip_x - side * 2.0, tip_y + chi.ear_h * 0.30)
    inner.lineTo(bx + side * chi.ear_w * 0.24, by + 3.0)
    inner.closeSubpath()
    p.setPen(Qt.NoPen)
    p.setBrush(pal.ear_inner)
    p.drawPath(inner)


def _eye_shape(p: QPainter, cx: float, cy: float, r: float, style: str,
               pal: Palette, side: int) -> None:
    """一只眼的所有情绪态。Q 版的核心是"大 + 低位 + 双高光"，不画写实眼裂。"""
    if style == "blink":
        p.setPen(_pen(pal.eye, 3.6))
        p.drawLine(QPointF(cx - r * 0.8, cy), QPointF(cx + r * 0.8, cy))
        return
    if style == "closed":                        # 睡觉：向下弯的温柔弧线
        path = QPainterPath()
        path.moveTo(cx - r * 0.9, cy - r * 0.25)
        path.cubicTo(cx - r * 0.3, cy + r * 0.75, cx + r * 0.3, cy + r * 0.75,
                     cx + r * 0.9, cy - r * 0.25)
        p.setPen(_pen(pal.eye, 3.8))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        return
    if style == "happy":                         # 开心：向上弯的 ^ 弧
        path = QPainterPath()
        path.moveTo(cx - r * 0.95, cy + r * 0.35)
        path.cubicTo(cx - r * 0.3, cy - r * 0.95, cx + r * 0.3, cy - r * 0.95,
                     cx + r * 0.95, cy + r * 0.35)
        p.setPen(_pen(pal.eye, 4.0))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        return
    if style == "hurt":                          # 疼：挤紧的 >< （不是卡通叉号）
        p.setPen(_pen(pal.eye, 3.8))
        k = 1 if side < 0 else -1
        p.drawLine(QPointF(cx - r * 0.7 * k, cy - r * 0.6), QPointF(cx + r * 0.5 * k, cy))
        p.drawLine(QPointF(cx + r * 0.5 * k, cy), QPointF(cx - r * 0.7 * k, cy + r * 0.6))
        return

    if style == "squint":                          # 小跑：半闭的聚焦眼，比"皱眉"可爱
        # 瞳球压扁＋斜眉试过：两团黑连成一片像戴墨镜，且表情读出来是"凶"不是"使劲"。
        ry = r * 0.74
        cy2 = cy + r * 0.14
        p.setPen(_pen(pal.outline, 2.0))
        p.setBrush(pal.eye)
        p.drawEllipse(QPointF(cx, cy2), r * 0.92, ry)
        p.setPen(Qt.NoPen)
        core = QRadialGradient(QPointF(cx, cy2 + ry * 0.15), r * 1.0)
        core.setColorAt(0.0, _alpha(pal.iris, 200))
        core.setColorAt(0.55, _alpha(pal.iris, 60))
        core.setColorAt(1.0, _alpha(pal.eye, 0))
        p.setBrush(core)
        p.drawEllipse(QPointF(cx, cy2), r * 0.92, ry)
        p.setBrush(_alpha(_c("#FFFFFF"), 230))
        p.drawEllipse(QPointF(cx - r * 0.28, cy2 - ry * 0.34), r * 0.24, ry * 0.36)
        p.setBrush(_alpha(_c("#FFFFFF"), 160))
        p.drawEllipse(QPointF(cx + r * 0.30, cy2 + ry * 0.30), r * 0.14, ry * 0.20)
        k = 1 if side < 0 else -1
        p.setPen(_pen(pal.outline, 3.0))            # 上睑压在瞳球上沿：内低外高，轻微就够
        p.drawLine(QPointF(cx - r * 0.95 * k, cy2 - ry * 0.95),
                   QPointF(cx + r * 0.95 * k, cy2 - ry * 0.62))
        return

    # open / sad / up：大椭圆 + 虹膜暖芯 + 双高光
    ry = r * (0.86 if style == "sad" else 1.12)
    p.setPen(_pen(pal.outline, 2.0))
    p.setBrush(pal.eye)
    p.drawEllipse(QPointF(cx, cy), r, ry)
    p.setPen(Qt.NoPen)
    core = QRadialGradient(QPointF(cx, cy + ry * 0.15), r * 1.1)
    core.setColorAt(0.0, _alpha(pal.iris, 210))
    core.setColorAt(0.55, _alpha(pal.iris, 70))
    core.setColorAt(1.0, _alpha(pal.eye, 0))
    p.setBrush(core)
    p.drawEllipse(QPointF(cx, cy + ry * 0.12), r * 0.86, ry * 0.86)
    white = _c("#FFFFFF")
    p.setBrush(_alpha(white, 235))
    p.drawEllipse(QPointF(cx - r * 0.34, cy - ry * 0.40), r * 0.34, r * 0.34)
    p.setBrush(_alpha(white, 200))
    p.drawEllipse(QPointF(cx + r * 0.34, cy + ry * 0.34), r * 0.17, r * 0.17)
    # 下眼睑反光：只有这一颗用低不透明度，眼睛才会"湿"
    p.setBrush(_alpha(white, 96))
    p.drawEllipse(QPointF(cx + r * 0.04, cy + ry * 0.70), r * 0.30, r * 0.11)
    if style == "sad":                           # 上眼睑压下来一点，眼神就垮了
        p.setPen(_pen(pal.outline, 3.2))
        path = QPainterPath()
        path.moveTo(cx - r * 0.95, cy - ry * 0.55 + side * 2.0)
        path.lineTo(cx + r * 0.95, cy - ry * 0.20 - side * 2.0)
        p.drawPath(path)


def _draw_eyes(p: QPainter, pal: Palette, chi: Chibi, head_cx: float,
               head_cy: float, cfg: dict) -> None:
    dy = chi.eye_dy + cfg.get("eye_dy", 0.0)
    for side in (-1, 1):
        cx = head_cx + side * chi.eye_dx
        _eye_shape(p, cx, head_cy + dy, chi.eye_r, cfg["eye"], pal, side)


def _draw_face(p: QPainter, pal: Palette, chi: Chibi, head_cx: float,
               head_cy: float, cfg: dict, rng: random.Random) -> None:
    """口吻浅色块 + 迷你鼻 + ω 小嘴 + 腮红 + 极淡胡须。"""
    if chi.muzzle_patch:
        _soft_patch(p, head_cx, head_cy + chi.eye_dy + chi.eye_r * 0.95,
                    chi.nose_w * 1.9, chi.nose_w * 1.15, pal.belly, 215)
    ny = head_cy + chi.eye_dy + chi.eye_r * 0.72
    if chi.ear == "round":                       # 猴：小鼻孔而不是三角鼻
        p.setPen(Qt.NoPen)
        p.setBrush(pal.nose)
        p.drawEllipse(QPointF(head_cx - chi.nose_w * 0.30, ny), chi.nose_w * 0.20,
                      chi.nose_w * 0.26)
        p.drawEllipse(QPointF(head_cx + chi.nose_w * 0.30, ny), chi.nose_w * 0.20,
                      chi.nose_w * 0.26)
    else:
        nose = QPainterPath()
        nose.moveTo(head_cx - chi.nose_w / 2.0, ny - chi.nose_w * 0.28)
        nose.lineTo(head_cx + chi.nose_w / 2.0, ny - chi.nose_w * 0.28)
        nose.lineTo(head_cx, ny + chi.nose_w * 0.34)
        nose.closeSubpath()
        _fill(p, nose, pal.nose, _pen(pal.outline, 1.8))
        # 鼻头高光：一小块低不透明度白，没它鼻头会看成一块墨
        p.setPen(Qt.NoPen)
        p.setBrush(_alpha(_c("#FFFFFF"), 110))
        p.drawEllipse(QPointF(head_cx - chi.nose_w * 0.16, ny - chi.nose_w * 0.14),
                      chi.nose_w * 0.16, chi.nose_w * 0.11)
    _draw_mouth(p, pal, chi, head_cx, ny + chi.nose_w * 0.55, cfg, rng)
    if cfg.get("blush", True):
        blush_y = head_cy + chi.eye_dy + chi.eye_r * 1.55
        for side in (-1, 1):
            grad = QRadialGradient(QPointF(head_cx + side * chi.eye_dx * 1.85, blush_y),
                                   chi.eye_r * 0.95)
            grad.setColorAt(0.0, _alpha(pal.blush, 120))
            grad.setColorAt(1.0, _alpha(pal.blush, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(grad)
            p.drawEllipse(QPointF(head_cx + side * chi.eye_dx * 1.85, blush_y),
                          chi.eye_r * 0.95, chi.eye_r * 0.60)
    if cfg.get("whiskers", True):
        _draw_whiskers(p, pal, chi, head_cx, ny)


def _draw_mouth(p: QPainter, pal: Palette, chi: Chibi, cx: float, cy: float,
                cfg: dict, rng: random.Random) -> None:
    style = cfg["mouth"]
    if style == "smile":                         # ω 形：上唇两瓣
        path = QPainterPath()
        path.moveTo(cx - 8.0, cy)
        path.cubicTo(cx - 5.0, cy + 5.0, cx - 1.5, cy + 5.0, cx, cy + 1.0)
        path.cubicTo(cx + 1.5, cy + 5.0, cx + 5.0, cy + 5.0, cx + 8.0, cy)
        p.setPen(_pen(pal.outline, 2.6))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        return
    if style == "open":
        w, h = 7.0, 6.0 + cfg.get("open_scale", 0.0)
        _fill(p, _ellipse_path(cx, cy + h * 0.35, w, h), _c("#8C4A4A"),
              _pen(pal.outline, 2.2))
        p.setPen(Qt.NoPen)
        p.setBrush(_alpha(pal.blush, 230))
        p.drawEllipse(QPointF(cx, cy + h * 0.75), w * 0.55, h * 0.42)
        return
    if style == "gape":                          # 吃东西：大口 + 舌头
        _fill(p, _ellipse_path(cx, cy + 3.0, 10.0, 8.5), _c("#8C4A4A"),
              _pen(pal.outline, 2.4))
        p.setPen(Qt.NoPen)
        p.setBrush(_alpha(pal.blush, 235))
        p.drawEllipse(QPointF(cx, cy + 7.5), 6.0, 4.0)
        return
    if style == "frown":                         # 撇嘴用路径而不是 drawArc，避开弧向歧义
        path = QPainterPath()
        path.moveTo(cx - 7.0, cy + 2.5)
        path.cubicTo(cx - 3.0, cy - 3.0, cx + 3.0, cy - 3.0, cx + 7.0, cy + 2.5)
        p.setPen(_pen(pal.outline, 2.6))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        return
    if style == "flat":
        p.setPen(_pen(pal.outline, 2.6))
        p.drawLine(QPointF(cx - 6.0, cy + 1.0), QPointF(cx + 6.0, cy + 1.0))
        return
    if style == "sleep":
        path = QPainterPath()
        path.addEllipse(QPointF(cx, cy + 1.5), 3.2, 2.6 + math.sin(rng.random()) * 0.4)
        p.setPen(_pen(pal.outline, 2.2))
        p.setBrush(_c("#8C4A4A"))
        p.drawPath(path)
        return
    # grimace：紧绷的小波浪
    path = QPainterPath()
    path.moveTo(cx - 8.0, cy + 1.0)
    for i, dx in enumerate((-4.0, 0.0, 4.0)):
        path.lineTo(cx + dx, cy + (1.0 if i % 2 else 4.0))
    p.setPen(_pen(pal.outline, 2.4))
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)


def _draw_whiskers(p: QPainter, pal: Palette, chi: Chibi, cx: float, cy: float) -> None:
    """两根、短、淡、下坠——上一版六根 40px 直白线是"诡异"的主要来源。"""
    p.setPen(_pen(_alpha(pal.whisker, 78), 2.0))
    for side in (-1, 1):
        for dy in (-1.0, 3.0):
            path = QPainterPath()
            path.moveTo(cx + side * chi.nose_w * 1.5, cy + dy)
            path.cubicTo(cx + side * chi.nose_w * 3.0, cy + dy - 1.0,
                         cx + side * 30.0, cy + dy + 1.0,
                         cx + side * 34.0, cy + dy + 5.0)
            p.drawPath(path)


def _draw_stripes(p: QPainter, pal: Palette, head_cx: float, head_cy: float,
                  head_r: float) -> None:
    """额前三道 + 颊侧两道：圆头短线，压在剪影内。"""
    p.save()
    p.setClipPath(_ellipse_path(head_cx, head_cy, head_r, head_r))
    p.setPen(_pen(_alpha(pal.stripe, 130), 6.0))
    for dx, tilt in ((-12.0, -14.0), (0.0, 0.0), (12.0, 14.0)):
        path = QPainterPath()
        path.moveTo(head_cx + dx, head_cy - head_r * 0.92)
        path.lineTo(head_cx + dx + tilt * 0.3, head_cy - head_r * 0.52)
        p.drawPath(path)
    p.restore()


def _crown_fluff(p: QPainter, pal: Palette, chi: Chibi, head_cx: float,
                 head_cy: float, rng: random.Random) -> None:
    """头顶几撮短弧：只暗示毛流方向，不逐根画（256px 下逐根只会变脏）。

    随机数只决定每撮的微小长度差，种子仍是 `皮肤|动作`（同一动作六帧共用），
    重跑逐字节一致。
    """
    p.save()
    p.setPen(_pen(pal.light, INNER_W))
    p.setBrush(Qt.NoBrush)
    top = head_cy - chi.head_r * 0.96
    for i in range(3):
        dx = (i - 1) * chi.head_r * 0.20
        lean = 5.0 + rng.random() * 2.0
        path = QPainterPath()
        path.moveTo(head_cx + dx, top + 5.0)
        path.cubicTo(head_cx + dx + 2.0, top - 3.0,
                     head_cx + dx + 4.0, top - 5.0,
                     head_cx + dx + lean, top - 1.0)
        p.drawPath(path)
    p.restore()


def _tapered_curve(p: QPainter, pts: list[QPointF], w0: float, w1: float,
                   color: QColor, rng: random.Random, fluff: float = 0.0,
                   fluff_color: QColor | None = None) -> None:
    """分段的锥形曲线：等宽圆头笔刷画不出“由根到尖”的尾巴。

    整段用同一个颜色：逐段交替 shade/base 会让尾巴看成一条分节的虫。
    """
    steps = len(pts) - 1
    for i in range(steps):
        w = w0 + (w1 - w0) * (i / steps)
        p.setPen(_pen(color, w))
        p.drawLine(pts[i], pts[i + 1])
    if fluff > 0:                                # 尾尖一撮绒
        tip = pts[-1]
        p.setPen(_pen(fluff_color or color, fluff * 0.8))
        for k in range(5):
            ang = rng.uniform(0.0, math.pi * 2.0)
            p.drawLine(tip, QPointF(tip.x() + math.cos(ang) * fluff,
                                    tip.y() + math.sin(ang) * fluff))


def _cheek_tufts(p: QPainter, pal: Palette, head_cx: float, head_cy: float,
                 head_r: float) -> None:
    """脸两侧各两簇毛：整圈软边会在小尺寸下糊成“太阳花”，只留脸侧才干净。"""
    p.save()
    p.setPen(_pen(pal.base, 4.2))
    p.setBrush(Qt.NoBrush)
    for side in (-1, 1):
        for dy in (-1.0, 6.0):
            x0 = head_cx + side * head_r * 0.88
            y0 = head_cy + head_r * 0.30 + dy
            p.drawLine(QPointF(x0, y0), QPointF(x0 + side * 8.0, y0 + 5.0))
    p.restore()


def _tail_points(chi: Chibi, cfg: dict, side: int) -> list[QPointF]:
    """三皮肤共用“贴地拖出体侧”这一条曲线，只换参数。

    尾根留在身体轮廓内（尾巴画在身体之前，根部被盖住），只让后半段钻出来。
    试过把柴犬卷尾画成腰侧的圆环——看上去是个马克杯把手，不是尾巴。
    （尾根偏移, 步长, 臀高比, 翘起基数, 翘起系数, 上抽强度）
    """
    spec = {"bushy": (0.18, 7.2, 0.38, 4.0, 9.0, 0.50),
            "curl": (0.16, 6.4, 0.30, 5.0, 10.0, 0.55),
            "thin": (0.16, 7.4, 0.36, 6.0, 12.0, 0.62)}[chi.tail]
    root_dx, step, hip, base, span, curl = spec
    sway = cfg["tail_sway"]
    lift = cfg["tail_lift"]
    ground = chi.body_cy + chi.body_h * hip
    rise = base + lift * span
    pts = []
    for i in range(9):
        out = max(0.0, float(i) - 4.0)          # 后半段才钻出身体轮廓
        pts.append(QPointF(CX + side * (chi.body_w * root_dx + i * step + sway * out * 0.9),
                           ground + i * 1.2 - out * out * curl * (rise / 4.0)))
    return pts


def _draw_tail(p: QPainter, pal: Palette, chi: Chibi, cfg: dict,
               rng: random.Random) -> None:
    side = -1 if cfg.get("tail_side", 1) > 0 else 1
    pts = _tail_points(chi, cfg, side)
    w0, w1 = {"bushy": (21.0, 10.0), "curl": (20.0, 13.0), "thin": (9.0, 4.0)}[chi.tail]
    _tapered_curve(p, pts, w0 + 3.4, w1 + 2.4, pal.shade, rng)      # 同色系底当边缘
    _tapered_curve(p, pts, w0, w1, pal.base, rng, chi.fluff, pal.light)
    # 尾上不加环纹：256px 下两段横纹会把尾巴看成一条分节的虫


def _draw_paws(p: QPainter, pal: Palette, chi: Chibi, cfg: dict) -> None:
    """两只前爪：浅色小椭圆 + 两道趾线，交代"手短"的幼态。

    走/跑时两爪按左右反相交替前伸并抬起（6 帧步态唯一能看到“迈步”的部位——
    Q 版本就不画腿，靠爪交代落地）；伸懒腰时两爪对称举高分开，露出肉垫。
    """
    y = chi.body_cy + chi.body_h * 0.40
    swing = cfg.get("paw_swing", 0.0)
    amp = cfg.get("paw_amp", 0.0)
    lift = cfg.get("paw_lift", 0.0)
    spread = cfg.get("paw_spread", 0.0)
    raise_both = cfg.get("paw_raise", 0.0)         # 对称举高：与步态的反相抬爪互斥
    for side in (-1, 1):
        reach = swing * -side                      # 反相：一爪前伸时另一爪在后
        dx = side * chi.body_w * 0.22 + side * spread + reach * amp \
            + cfg.get("paw_shift", 0.0)            # 两爪同向平移（侧视前伸）
        py = y - max(0.0, reach) * lift - raise_both
        path = _ellipse_path(CX + dx, py, 13.0, 9.5)
        _fill(p, path, pal.belly, _pen(pal.outline, 3.6))
        if y - py > 1.0:                            # 抬离地面那一两帧：露肉垫而不是趾线
            _draw_paw_pads(p, pal, CX + dx, py)
            continue
        p.setPen(_pen(_alpha(pal.shade, 150), 1.8))
        p.drawLine(QPointF(CX + dx - 3.0, py + 1.0), QPointF(CX + dx - 3.0, py + 5.5))
        p.drawLine(QPointF(CX + dx + 3.0, py + 1.0), QPointF(CX + dx + 3.0, py + 5.5))


def _draw_paw_pads(p: QPainter, pal: Palette, cx: float, cy: float) -> None:
    """肉垫：1 块掌垫 + 4 颗趾垫，粉色半透明（PRD v2.0 C9）。

    只在爪子抬离地面时画：一直露着等于给每一帧多贴了两块粉斑，站着的时候
    爪底是看不见的。
    """
    pad = _mix(pal.blush, pal.belly, 0.45)
    p.setPen(Qt.NoPen)
    p.setBrush(_alpha(pad, 225))
    p.drawEllipse(QPointF(cx, cy + 2.2), 5.4, 4.2)                       # 掌垫
    for dx in (-6.2, -2.1, 2.1, 6.2):
        p.drawEllipse(QPointF(cx + dx, cy - 3.4 + abs(dx) * 0.16), 1.7, 1.7)  # 趾垫


def _draw_bowl(p: QPainter, rng: random.Random, kibble: int) -> None:
    """食盆：Q 版就是一只浅口碗 + 几颗粮，随帧数减少（吃的过程可见）。"""
    bx, by = CX, BASE_Y - 12.0
    bowl = QPainterPath()
    bowl.moveTo(bx - 30.0, by - 9.0)
    bowl.cubicTo(bx - 26.0, by + 14.0, bx + 26.0, by + 14.0, bx + 30.0, by - 9.0)
    bowl.closeSubpath()
    _fill(p, bowl, _c("#9FC6D8"), _pen(_c("#5C8FA3"), 3.2))
    p.setPen(Qt.NoPen)
    p.setBrush(_c("#CFE6EF"))
    p.drawEllipse(QPointF(bx, by - 9.0), 30.0, 7.0)
    for i in range(max(0, kibble)):
        ang = rng.uniform(0.0, math.pi * 2.0)
        r = rng.uniform(0.0, 16.0)
        p.setBrush(_c("#B07A45"))
        p.drawEllipse(QPointF(bx + math.cos(ang) * r, by - 9.0 + math.sin(ang) * 3.0),
                      3.4, 2.6)


def _z_glyph(p: QPainter, x: float, y: float, size: float, alpha: int) -> None:
    """Z 用三条 drawLine：drawPolygon 会闭合折线，画出来是蝴蝶结。"""
    p.setPen(_pen(_alpha(_c("#5C6B7A"), alpha), size * 0.18))
    half = size / 2.0
    p.drawLine(QPointF(x - half, y - half), QPointF(x + half, y - half))
    p.drawLine(QPointF(x + half, y - half), QPointF(x - half, y + half))
    p.drawLine(QPointF(x - half, y + half), QPointF(x + half, y + half))


# --------------------------------------------------------------------------
# 姿态表：只管相位与情绪，不管物种
# --------------------------------------------------------------------------

def _rise_fall(phase: float) -> float:
    """快升慢落的循环波：吸一口气是快的，吐出来是慢的。

    正弦波满足 `f(t+0.5) == -f(t)`，6 帧里第 1 与第 4 帧（t=0 与 t=0.5）永远同时取
    0，叠二次谐波也改不掉这个性质（谐波同样奇对称）——实测 idle 的 01.png 与
    03.png 逐字节相同。升段只占 0.34 个周期、落段占 0.66 个，取值天然不对称。
    两段都用余弦缓动，接缝处速度连续，播起来不会“弹一下”。
    """
    p = phase % 1.0
    rise = 0.34
    if p < rise:
        return -1.0 + 2.0 * (p / rise)
    q = (p - rise) / (1.0 - rise)
    return 1.0 - 2.0 * (1.0 - math.cos(q * math.pi)) / 2.0


def _offset_wave(phase: float, offset: float = 0.22) -> float:
    """同形但错开一截的次级波：摆尾、歪头用。

    主波在 t=0 与 t=0.5 都取 -1，只用它驱动多个通道时这两帧仍会碰在一起；
    错开 0.22 个周期把奇对称破掉，六帧才两两不同。
    """
    return _rise_fall(phase + offset)


def _stride_bounce(phase: float) -> float:
    """一步一下的落地弹跳（只向下），每步落地快、支撑期慢沉。"""
    return -abs(_rise_fall(2.0 * phase))


def _pose(action: str, index: int) -> dict:
    phase = index / FRAMES
    wave = _rise_fall(phase)
    sway = _offset_wave(phase)
    bounce = _stride_bounce(phase)
    cfg = {
        "bob": 0.0, "tilt": 0.0, "squash": 1.0, "head_dy": 0.0, "ear_droop": 0.0,
        "eye": "open", "mouth": "smile", "tail_sway": sway * 0.35, "tail_lift": 0.55,
        "tail_side": 1.0, "blush": True, "whiskers": True, "paw_shift": 0.0,
        "paw_swing": 0.0, "paw_amp": 0.0, "paw_lift": 0.0, "paw_spread": 0.0,
        "open_scale": 0.0, "bowl": False, "kibble": 0, "zzz": 0,
        "sweat": False, "tear": False, "eye_dy": 0.0,
    }
    if action == "idle":
        # 呼吸幅度按实测调：1.6px 在 128px 窗口里只有 0.7px，跟静图无异
        cfg["bob"] = wave * 4.0
        cfg["head_dy"] = wave * 3.0
        cfg["squash"] = 1.0 + wave * 0.022
        cfg["tail_sway"] = sway * 0.6
        cfg["eye"] = "blink" if index == 4 else "open"
    elif action == "walk":
        # 6 帧 = 一个完整步态周期：接触-下压-经过-上扬-前进-另一侧接触
        cfg["bob"] = bounce * 3.5
        cfg["tilt"] = wave * 2.4
        cfg["squash"] = 1.0 + wave * 0.020
        cfg["paw_swing"] = wave
        cfg["paw_amp"] = 12.0
        cfg["paw_lift"] = 7.0
        cfg["head_dy"] = -sway * 2.0
        cfg["tail_sway"] = sway * 0.7
        cfg["tail_lift"] = 0.8
    elif action == "run":
        # 与小跑同构但幅度拉大：前倾、身体起伏更高、爪摆幅更大
        cfg["bob"] = bounce * 5.5
        cfg["tilt"] = 5.0 + wave * 3.0
        cfg["squash"] = 1.0 + wave * 0.030
        cfg["paw_swing"] = wave
        cfg["paw_amp"] = 16.0
        cfg["paw_lift"] = 10.0
        cfg["head_dy"] = -2.0 + sway * 1.5
        cfg["eye"] = "squint"                    # 小跑时眼睛微眯，不然与走路同一个表情
        cfg["tail_sway"] = sway * 1.1
        cfg["tail_lift"] = 1.25
    elif action == "stretch":
        # 正面视角画不出侧视的"前爪趴低、屁股翘起"（身体是个对称梨），
        # 改成同样可读的"举爪打哈欠"：两爪对称举高露肉垫 + 张口 + 闭眼。
        # 单峰也不能用 sin：`sin((i+0.5)π/(N+1))` 关于 π/2 对称，实测第 2=6、第 3=5 帧
        # 逐字节相同。改成幂次偏峰：峰仍然在中间，但两侧取值不再镜像。
        t = 1.0 - ((index + 0.5) / (FRAMES + 1)) ** 0.85
        cfg["squash"] = 1.0 + 0.10 * t
        cfg["head_dy"] = 16.0 * t
        cfg["paw_raise"] = 30.0 * t
        cfg["paw_spread"] = 16.0 * t
        cfg["ear_droop"] = 1.2 * t
        cfg["tail_lift"] = 2.2
        cfg["tail_sway"] = 0.0
        cfg["eye"] = "closed" if t > 0.8 else "open"
        cfg["mouth"] = "gape" if t > 0.8 else "smile"
    elif action == "sad":
        # 含泪不动=死图：靠垂头与耳朵轻微下垂交代“难过时也在呼吸”
        cfg.update(ear_droop=1.0 + abs(wave) * 0.4, eye="sad", mouth="frown",
                   head_dy=4.0 + wave * 4.0, bob=sway * 2.5,
                   tail_lift=0.05, tail_sway=sway * 0.3, blush=False,
                   tear=index in (1, 2, 3))
    elif action == "hurt":
        # 抖一下然后收住：不能写成 index % 2 的奇偶翻转，那是把六帧做成两种姿态，
        # 实测成对帧落盘字节完全相同（目检与静图无异），播起来是「顿顿顿」而不是疼。
        # 也不能衰减得太快：一圈平均差异低于 1.2% 就报静图，所以拖个摆尾兼打底。
        flinch = wave * (1.0 - index / (FRAMES + 1)) * 6.0
        cfg.update(ear_droop=1.5, eye="hurt", mouth="grimace", head_dy=2.0 + wave * 1.5,
                   bob=flinch + sway * 0.8, squash=1.04 + wave * 0.012, tail_lift=0.1,
                   blush=False, tail_sway=flinch * 0.25 + sway * 0.5)
    elif action == "think":
        # 歪头随呼吸摆动 + 一次眨眼：只靠头顶那滴汗看不出一只在思考
        cfg.update(mouth="flat", sweat=index >= 3, eye_dy=-2.0,
                   eye="blink" if index == 2 else "open",
                   tilt=-4.0 + sway * 2.5, bob=wave * 1.5, head_dy=wave * 1.5,
                   tail_lift=0.35, tail_sway=sway * 0.45)
    elif action == "eat":
        # 啄食幅度拉大：头不动只盆里变少，128px 下看不出在吃
        cfg.update(mouth="gape", eye="happy", bowl=True, kibble=FRAMES - index,
                   head_dy=12.0 + wave * 9.0, squash=1.0 + wave * 0.03,
                   tail_lift=0.9, tail_sway=sway * 0.5, blush=True)
    elif action == "speak":
        cfg.update(mouth="open", open_scale=(index % 2) * 2.5 + abs(wave) * 2.0,
                   eye="happy" if index in (1, 3, 5) else "open",
                   bob=wave * 3.0, head_dy=wave * 2.5, tilt=sway * 1.8,
                   tail_lift=1.0)
    elif action == "sleep":
        # 睡着靠“呼吸”交代：身体起伏必须看得见，否则就是张静图
        cfg.update(mouth="sleep", eye="closed", head_dy=16.0 + wave * 3.0,
                   squash=1.06 + wave * 0.05,
                   tail_lift=0.0, tail_sway=sway * 0.25, blush=False, whiskers=False,
                   zzz=min(3, index // 2 + 1), bob=wave * 3.5)
    return cfg


# --------------------------------------------------------------------------
# 整帧组装
# --------------------------------------------------------------------------

def render_frame(action: str, index: int, skin: str = "cat") -> QImage:
    """画一帧。action/index 非法时抛 ValueError（调用方是脚本，不做静默兜底）。"""
    if action not in ACTIONS:
        raise ValueError(f"unknown action: {action}")
    if not 0 <= index < FRAMES:
        raise ValueError(f"index out of range: {index}")
    pal, chi = SKINS[skin]
    cfg = _pose(action, index)
    # 装饰随机数按 `皮肤|动作` 取种子，不带帧号：同一动作的六帧必须长在同一撮毛上。
    # 带帧号时身体外圈那 20 撮软毛每帧换位置，静态预览看不出来，播起来就是浑身发抖
    # （帧间差异实测 28%，修后降到 2% 量级）。
    rng = random.Random(zlib.crc32(f"{skin}|{action}".encode()))

    image = QImage(SIZE, SIZE, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.Antialiasing)

    head_cy = chi.head_cy + cfg["head_dy"] + cfg["bob"]
    body_cy = chi.body_cy + cfg["bob"] * 0.35
    chi = Chibi(**{**chi.__dict__, "body_cy": body_cy})   # 尾巴/爪子跟着身体一起动

    _contact_shadow(p, BASE_Y - 2.0, chi.body_w * 0.52)

    p.save()
    p.translate(CX, head_cy * 0.4 + body_cy * 0.6)
    p.rotate(cfg["tilt"])
    p.translate(-CX, -(head_cy * 0.4 + body_cy * 0.6))

    _draw_tail(p, pal, chi, cfg, rng)          # 先画尾：根部被身体盖住，只露体侧上半截
    body = _body_path(chi, cfg["squash"])
    _fluff_behind(p, body, pal.base, chi.fluff * 0.8, rng, 20)
    _volume(p, body, pal, (CX - chi.body_w * 0.25, body_cy - chi.body_h * 0.30))
    _stroke(p, body, pal.outline, OUTLINE_W)
    if chi.body_mark == "bib":
        # 腹部奶油块：软边缘而不是硬椭圆，否则像贴了一块膏药
        p.save()
        p.setClipPath(body)
        _soft_patch(p, CX, body_cy + chi.body_h * 0.12,
                    chi.body_w * 0.30, chi.body_h * 0.34, pal.belly, 225)
        p.restore()
    _draw_paws(p, pal, chi, cfg)

    for side in (-1, 1):                       # 耳朵在头后面，先画
        _draw_ear(p, pal, chi, side, CX, head_cy, cfg["ear_droop"])

    head = _ellipse_path(CX, head_cy, chi.head_r, chi.head_r * 0.96)
    if chi.cheek_tufts:
        _cheek_tufts(p, pal, CX, head_cy, chi.head_r)   # 先画，根部会被头体盖住
    _volume(p, head, pal, (CX - chi.head_r * 0.35, head_cy - chi.head_r * 0.40))
    _stroke(p, head, pal.outline, OUTLINE_W)
    _crown_fluff(p, pal, chi, CX, head_cy, rng)          # 头顶几撮：压在描边内侧
    if chi.stripes:
        _draw_stripes(p, pal, CX, head_cy, chi.head_r)

    _draw_eyes(p, pal, chi, CX, head_cy, cfg)
    _draw_face(p, pal, chi, CX, head_cy, cfg, rng)
    if cfg["tear"]:
        p.setPen(Qt.NoPen)
        p.setBrush(_alpha(_c("#9CD3E8"), 225))
        tx = CX - chi.eye_dx * 0.78
        ty = head_cy + chi.eye_dy + chi.eye_r * 1.25
        p.drawEllipse(QPointF(tx, ty), 3.0, 4.4)
        p.drawEllipse(QPointF(tx, ty - chi.eye_r * 0.55), 1.8, 2.2)
    if cfg["sweat"]:
        p.setPen(Qt.NoPen)
        p.setBrush(_alpha(_c("#A9DCEC"), 225))
        p.drawEllipse(QPointF(CX + chi.head_r * 0.62, head_cy - chi.head_r * 0.42),
                      4.0, 6.0)
    p.restore()

    if cfg["bowl"]:                               # 盆必须在身体之后画，否则被完全挡住
        _draw_bowl(p, rng, cfg["kibble"])

    for i in range(cfg["zzz"]):                # 睡觉的 Z 逐级升空
        size = 10.0 + i * 5.0
        _z_glyph(p, CX + chi.head_r * 0.75 + i * 12.0,
                 head_cy - chi.head_r * 0.75 - i * 14.0, size, 210 - i * 55)

    p.end()
    return image


# --------------------------------------------------------------------------
# 落盘与目检
# --------------------------------------------------------------------------

def write_assets(out_dir: Path, skins: list[str], actions: tuple[str, ...] = ACTIONS) -> int:
    """写出 `{out_dir}/pet/{skin}/{action}/01..05.png`，返回帧数。"""
    total = 0
    for skin in skins:
        for action in actions:
            directory = out_dir / "pet" / skin / action
            directory.mkdir(parents=True, exist_ok=True)
            for i in range(FRAMES):
                image = render_frame(action, i, skin)
                target = directory / f"{i + 1:02d}.png"
                if not image.save(str(target), "PNG"):
                    raise OSError(f"素材写入失败：{target}")
                total += 1
    return total


def write_preview(path: Path, skin: str) -> Path:
    """把 10 动作 × 6 帧拼成一张总览图供目检（临时产物，用后即删）。"""
    sheet = QImage(SIZE * FRAMES, SIZE * len(ACTIONS), QImage.Format_ARGB32)
    sheet.fill(Qt.transparent)
    p = QPainter(sheet)
    for row, action in enumerate(ACTIONS):
        for col in range(FRAMES):
            p.drawImage(col * SIZE, row * SIZE, render_frame(action, col, skin))
    p.end()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not sheet.save(str(path), "PNG"):
        raise OSError(f"预览图写入失败：{path}")
    return path


def imported_actions(out_dir: Path, skin: str, actions: tuple[str, ...]) -> list[str]:
    """认出哪些动作的落盘帧是外部导入的（import_pet_images.py 写的 `.imported` 标记）。

    光看 PNG 分不出来源（Qt 把两种图都归一成 ARGB32，实测拿格式当判据会漏报），
    所以只信导入器留下的标记文件。不认出来直接重生会把人家用钱换来的图静默
    盖掉——实测踩过一次：带 --preview 跑全皮肤，三个 AI 动作全回到程序版。
    """
    return [action for action in actions
            if (out_dir / "pet" / skin / action / ".imported").exists()]


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="生成桌宠 Q 版序列帧素材")
    parser.add_argument("--skin", default="cat",
                        help=f"皮肤 id，或 all（可选：{', '.join(SKINS)}）")
    parser.add_argument("--out", default=str(root / "assets"), help="素材根目录")
    parser.add_argument("--preview", action="store_true",
                        help="额外输出 tmp/preview-{skin}.png 总览图（临时产物）")
    parser.add_argument("--action", default="",
                        help=f"只重生这些动作（逗号分隔），缺省全部（可选：{', '.join(ACTIONS)}）")
    parser.add_argument("--force", action="store_true",
                        help="连外部导入的素材一起重生（默认跳过，不静默盖掉 AI 图）")
    args = parser.parse_args(argv)

    if args.skin == "all":
        skins = list(SKINS)
    elif args.skin in SKINS:
        skins = [args.skin]
    else:
        print(f"未知皮肤：{args.skin}（可选：all, {', '.join(SKINS)}）")
        return 2

    if args.action:
        wanted = tuple(a.strip() for a in args.action.split(",") if a.strip())
        unknown = [a for a in wanted if a not in ACTIONS]
        if unknown:
            print(f"未知动作：{', '.join(unknown)}（可选：{', '.join(ACTIONS)}）")
            return 2
        actions = tuple(a for a in ACTIONS if a in wanted)
    else:
        actions = ACTIONS

    out_dir = Path(args.out)
    foreign = imported_actions(out_dir, skins[0], actions) if len(skins) == 1 else []
    # 多皮肤时不逐个探（当前仅 cat 有 AI 图）；单皮肤才做精确保护
    if foreign and not args.force:
        actions = tuple(a for a in actions if a not in foreign)
        print(f"跳过外部导入的动作（不静默盖掉 AI 图）：{', '.join(foreign)}"
              f"\n要连它们一起重生：加 --force，或先删对应目录里的 .imported")
        if not actions:
            print("没有程序绘制的动作可重生，未写任何文件。")
            return 0
    count = write_assets(out_dir, skins, actions)
    print(f"已生成 {count} 帧 -> {out_dir / 'pet'}"
          f"（skins={','.join(skins)}, actions={','.join(actions)}）")
    if args.preview:
        for skin in skins:
            print(f"目检预览：{write_preview(root / 'tmp' / f'preview-{skin}.png', skin)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
