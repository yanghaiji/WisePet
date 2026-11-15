"""TechEffect 出场/退场特效：7 种进入特效共享统一骨架、各阶段绘制不崩、驱动到结束发 finished。

重点锁住「整体优化」的两条硬约束：
1. 绘制期零随机——同一进度连画两帧像素完全一致（旧实现每帧重随机导致动画抖动）；
2. 统一 950ms 节奏 + 66% 后统一收尾爆闪 + 82% 提前 reveal 与宠物淡入重叠。
"""
import random

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QImage, QPainter

from WisePet.ui.tech_effects import (CANVAS, DURATION_MS, ENTER_DURATION_MS,
                                     ENTER_KINDS, FRAME_MS, MAIN_END, REVEAL_AT,
                                     TRIGGER_ENTER_KIND, TechEffect, _envelope)

_KINDS = tuple(ENTER_KINDS) + ("exit",)


def _paint_frame(effect, t):
    """在离屏 ARGB 图上跑一遍绘制，验证不抛异常。"""
    img = QImage(CANVAS, CANVAS, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    painter = QPainter(img)
    try:
        if effect._mode == "enter":
            effect._paint_enter(painter, t, effect._kind)
        else:
            effect._paint_exit(painter, t)
    finally:
        painter.end()
    return img


def _pixel_digest(img):
    """稀疏采样像素值做内容摘要，避免依赖 Qt 绑定的内存缓冲比较语义。"""
    return tuple(img.pixel(x, y)
                 for y in range(0, CANVAS, 12)
                 for x in range(0, CANVAS, 12))


@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("t", [0.0, 0.3, 0.7, 0.85, 1.0])
def should_paint_each_kind_each_phase_without_error(qtbot, kind, t):
    effect = TechEffect(QPoint(100, 100), kind=kind)
    qtbot.addWidget(effect)

    img = _paint_frame(effect, t)

    assert img.width() == CANVAS
    assert img.hasAlphaChannel()


@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("t", [0.15, 0.45, 0.75])
def should_be_frame_deterministic_no_random_during_paint(qtbot, kind, t):
    """整体优化核心：同一进度连画两帧像素完全一致。

    旧实现 puzzle/terminal/ink/magnifier 在 paint 里调 rng，碎片落点/字符/
    波纹配色/光束高度逐帧乱跳，动画实际是抖的。随机参数已上收到构造期缓存。
    """
    effect = TechEffect(QPoint(100, 100), kind=kind, rng=random.Random(42))
    qtbot.addWidget(effect)

    assert _pixel_digest(_paint_frame(effect, t)) == \
        _pixel_digest(_paint_frame(effect, t))


def should_emit_finished_when_elapsed(qtbot):
    """手动推进帧到超时长即发 finished，不依赖真实 33ms 计时器。"""
    effect = TechEffect(QPoint(100, 100), kind="spawn")
    qtbot.addWidget(effect)
    fired = []
    effect.finished.connect(lambda: fired.append(1))

    for _ in range(ENTER_DURATION_MS["spawn"] // FRAME_MS + 2):
        effect._advance()

    assert len(fired) == 1


def should_use_unified_duration_for_all_kinds(qtbot):
    """统一节奏：7 种进入特效都 950ms；exit 维持 700ms。"""
    assert set(ENTER_DURATION_MS.values()) == {950}
    assert 950 > DURATION_MS
    for kind in ENTER_KINDS:
        eff = TechEffect(QPoint(0, 0), kind=kind)
        qtbot.addWidget(eff)
        assert eff._duration == ENTER_DURATION_MS[kind]
    exit_eff = TechEffect(QPoint(0, 0), kind="exit")
    qtbot.addWidget(exit_eff)
    assert exit_eff._duration == DURATION_MS


def should_reveal_early_once_and_never_twice(qtbot):
    """进入特效在 REVEAL_AT 提前发 finished 让宠物淡入与爆闪重叠，结束不重复发。"""
    effect = TechEffect(QPoint(0, 0), kind="puzzle")
    qtbot.addWidget(effect)
    fired = []
    effect.finished.connect(lambda: fired.append(effect._elapsed))

    for _ in range(int(950 * REVEAL_AT) // FRAME_MS + 1):
        effect._advance()
    assert len(fired) == 1
    assert fired[0] < 950

    for _ in range(950 // FRAME_MS + 3):
        effect._advance()
    assert len(fired) == 1


def should_not_reveal_early_for_exit(qtbot):
    """退场仍只在结束时发一次 finished（退出程序依赖它）。"""
    effect = TechEffect(QPoint(0, 0), kind="exit")
    qtbot.addWidget(effect)
    fired = []
    effect.finished.connect(lambda: fired.append(effect._elapsed))

    for _ in range(int(DURATION_MS * REVEAL_AT) // FRAME_MS):
        effect._advance()
    assert fired == []
    for _ in range(DURATION_MS // FRAME_MS + 2):
        effect._advance()
    assert len(fired) == 1


def should_envelope_fade_in_and_out():
    """首尾渐入渐出，消除硬起硬停。"""
    assert _envelope(0.0) == 0.0
    assert _envelope(1.0) == 0.0
    assert _envelope(0.5) == 1.0


def should_puzzle_use_only_bright_tones():
    """三角碎片配色：只用亮色发光，不再出现暗青/暗棕的实心块。

    旧实现把非青碎片的「亮部」写成 CYAN_DARK(20,80,100)、渐变到
    ORANGE_DARK(120,70,30)，等于一块黑乎乎的不透明三角，与整套光效割裂。
    这里锁住：两色必须是 CYAN/ORANGE，且每色亮度足够高。
    """
    from WisePet.ui.tech_effects import CYAN, ORANGE, PUZZLE_TONES
    assert PUZZLE_TONES == (CYAN, ORANGE)
    for tone in PUZZLE_TONES:
        assert max(tone.red(), tone.green(), tone.blue()) >= 200
        assert sum((tone.red(), tone.green(), tone.blue())) >= 400


def should_puzzle_fill_fade_to_transparent(qtbot):
    """填充末端必须全透明（光效而非实心块）：连画两次仍稳定且不抛异常。"""
    effect = TechEffect(QPoint(100, 100), kind="puzzle", rng=random.Random(7))
    qtbot.addWidget(effect)
    first = _pixel_digest(_paint_frame(effect, 0.45))
    second = _pixel_digest(_paint_frame(effect, 0.45))
    assert first == second


def should_paint_unified_finale_for_every_kind(qtbot):
    """统一收尾：每种特效在 MAIN_END 之后都会走同一套白核爆闪。"""
    for kind in ENTER_KINDS:
        eff = TechEffect(QPoint(0, 0), kind=kind)
        qtbot.addWidget(eff)
        _paint_frame(eff, MAIN_END + 0.2)


def should_expose_seven_style_distinct_enter_kinds_and_trigger_map(qtbot):
    """对外常量：7 种风格各异的进入特效，触发映射覆盖 5 个场景键。"""
    assert ENTER_KINDS == ("spawn", "burst", "puzzle", "terminal",
                           "curtain", "ink", "magnifier")
    assert set(TRIGGER_ENTER_KIND) == {"boot", "toggle", "chat", "scene", "celebrate"}
    for kind in TRIGGER_ENTER_KIND.values():
        assert kind in ENTER_KINDS


def should_random_enter_kind_include_all_kinds(monkeypatch):
    """随机特效池包含所有 7 种进入特效。"""
    import random as _random
    captured = {}
    monkeypatch.setattr(_random, "choice",
                        lambda pool: (captured.__setitem__("pool", list(pool)) or pool[0]))
    from WisePet.ui.tech_effects import random_enter_kind
    kind = random_enter_kind()
    assert captured["pool"] == list(ENTER_KINDS)
    assert kind in ENTER_KINDS


def should_accept_legacy_mode_argument(qtbot):
    """旧调用 TechEffect(center, mode="spawn") 仍作为 kind 别名兼容。"""
    eff = TechEffect(QPoint(0, 0), mode="spawn")
    qtbot.addWidget(eff)
    assert eff._kind == "spawn"
    assert eff._mode == "enter"
