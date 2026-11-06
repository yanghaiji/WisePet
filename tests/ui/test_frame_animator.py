"""FrameAnimator 冒烟：逐动作帧率、素材缺失与损坏降级、朝向镜像（PRD US-1/M1/v2.0 C11）。"""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap

from WisePet.domain import actions as domain_actions
from WisePet.domain.actions import ACTION_NAMES
from WisePet.domain.position import Facing
from WisePet.domain.state_machine import BehaviorState
from WisePet.infra.frame_loader import FrameAssetLoader
from WisePet.ui.frame_animator import (
    ACTION_BY_STATE,
    CANVAS_SIZE,
    FPS,
    FPS_BY_ACTION,
    FRAME_INTERVAL_MS,
    FrameAnimator,
    PlaceholderRenderer,
    action_for_state,
    frame_interval_ms,
)
from conftest import ASSETS_DIR


@pytest.fixture
def animator(qapp):
    anim = FrameAnimator(FrameAssetLoader(ASSETS_DIR))
    yield anim
    anim.stop()


def should_map_every_behavior_state_to_an_action_when_defined():
    assert set(ACTION_BY_STATE) == set(BehaviorState)


def should_have_a_frame_rate_for_every_action():
    """帧率表覆盖全部动作：漏配时不会静默回到 12FPS 而是这里会红。"""
    assert set(FPS_BY_ACTION) == set(ACTION_NAMES)


def should_share_the_domain_frame_rate_table_when_imported():
    """节拍只有一个真相源：ui 里改一份、app 的收场时长按另一份，就会把动画剪断。"""
    assert FPS_BY_ACTION is domain_actions.FPS_BY_ACTION
    assert frame_interval_ms is domain_actions.frame_interval_ms
    assert FPS == domain_actions.FPS_FALLBACK


@pytest.mark.parametrize("action", sorted(ACTION_NAMES))
def should_convert_fps_to_interval_when_action_known(action):
    assert frame_interval_ms(action) == round(1000 / FPS_BY_ACTION[action])


def should_fall_back_to_global_fps_when_action_unknown():
    assert frame_interval_ms("gallop") == FRAME_INTERVAL_MS == round(1000 / FPS)


def should_slow_down_sleep_and_speed_up_run_when_action_switches(animator):
    """6 帧统一帧数后，走/跑/睡的节奏差异全靠帧率表（PRD v2.0 C11）。"""
    animator.set_action("sleep")
    sleep_ms = animator.interval_ms
    animator.set_action("run")
    run_ms = animator.interval_ms
    animator.set_action("walk")
    walk_ms = animator.interval_ms

    assert sleep_ms > walk_ms > run_ms
    assert run_ms == frame_interval_ms("run")


def should_keep_interval_when_same_action_reselected(animator):
    animator.set_action("walk")
    before = animator.interval_ms

    animator.set_action("walk")

    assert animator.interval_ms == before


def should_use_real_frames_when_assets_present(animator):
    animator.set_action("idle")

    assert animator.action == "idle"
    assert not animator.uses_placeholder
    frame = animator.current_frame()
    assert not frame.isNull()
    assert frame.width() == frame.height() == 128


def should_fall_back_to_placeholder_when_asset_dir_empty(qapp, tmp_path):
    animator = FrameAnimator(FrameAssetLoader(tmp_path))
    animator.set_state(BehaviorState.SAD)

    assert animator.uses_placeholder
    assert not animator.current_frame().isNull()


def should_fall_back_to_placeholder_when_frame_file_broken(qapp, tmp_path):
    """素材损坏不崩溃：写一个假 PNG 文件（PRD US-1 验收 4）。"""
    broken = tmp_path / "pet" / "cat" / "idle"
    broken.mkdir(parents=True)
    (broken / "01.png").write_bytes(b"not a png")

    animator = FrameAnimator(FrameAssetLoader(tmp_path))
    animator.set_action("idle")

    assert not animator.current_frame().isNull()


def _write_frame(directory, color: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    pix = QPixmap(32, 32)
    pix.fill(QColor(color))
    pix.save(str(directory / "01.png"), "PNG")


def should_swap_frames_when_skin_loader_replaced(qapp, tmp_path):
    """换皮肤：set_loader 后必须读到新素材，而不是旧帧缓存（PRD v1.2 C3）。"""
    _write_frame(tmp_path / "pet" / "cat" / "idle", "#FF0000")
    _write_frame(tmp_path / "pet" / "dog" / "idle", "#00FF00")
    animator = FrameAnimator(FrameAssetLoader(tmp_path, "cat"))
    animator.set_action("idle")
    red = animator.current_frame().toImage().pixelColor(64, 64)

    animator.set_loader(FrameAssetLoader(tmp_path, "dog"))
    green = animator.current_frame().toImage().pixelColor(64, 64)

    assert red.name() == "#ff0000"
    assert green.name() == "#00ff00"
    assert animator.action == "idle"
    assert not animator.uses_placeholder


def should_use_placeholder_after_skin_cleared(qapp, tmp_path):
    """换到一个空皮肤时旧帧不得残留（回落也失败 → 占位）。"""
    _write_frame(tmp_path / "pet" / "cat" / "idle", "#FF0000")
    animator = FrameAnimator(FrameAssetLoader(tmp_path, "cat"))
    animator.set_action("idle")

    animator.set_loader(FrameAssetLoader(tmp_path / "nowhere", "monkey"))

    assert animator.uses_placeholder
    assert not animator.current_frame().isNull()


def should_emit_frame_ready_when_timer_ticks(animator, qtbot):
    animator.set_action("walk")
    with qtbot.waitSignal(animator.frame_ready, timeout=FRAME_INTERVAL_MS * 5):
        animator.start()


def should_emit_new_frame_when_facing_flipped(animator, qtbot):
    """镜像不改变尺寸且会重绘（走动折返时立即换头）。"""
    animator.set_action("walk")

    with qtbot.waitSignal(animator.frame_ready, timeout=200):
        animator.set_facing(Facing.LEFT)

    frame = animator.current_frame()
    assert not frame.isNull()
    assert frame.width() == 128


def should_mirror_each_frame_only_once_when_facing_left(animator):
    """镜像结果进缓存：向左走时每帧只做一次整幅变换，不能每拍重做。"""
    animator.set_action("walk")
    animator.set_facing(Facing.LEFT)
    frames = len(animator._frames)
    first = animator.current_frame()

    for _ in range(frames * 2):        # 整两圈：每帧都会被再次取到
        animator._advance()

    assert len(animator._mirror_cache) == frames
    assert animator.current_frame() is first


def should_drop_mirror_cache_when_skin_loader_replaced(animator, qapp, tmp_path):
    """换皮后旧镜像不得残留：否则向左走时播的还是旧皮肤的姿态。"""
    animator.set_action("idle")
    animator.set_facing(Facing.LEFT)
    animator.current_frame()
    assert animator._mirror_cache

    animator.set_loader(FrameAssetLoader(tmp_path, "dog"))

    assert animator._mirror_cache == {}


def _write_band_frame(directory, y0: int, y1: int, size: int) -> None:
    """画一段横色带当“角色”：只有 y0..y1 不透明，用来量缩放后的上下边界。"""
    directory.mkdir(parents=True, exist_ok=True)
    img = QImage(size, size, QImage.Format_RGBA8888)
    img.fill(Qt.transparent)
    painter = QPainter(img)
    try:
        painter.fillRect(0, y0, size, y1 - y0 + 1, QColor("#FF0000"))
    finally:
        painter.end()
    img.save(str(directory / "01.png"), "PNG")


def _opaque_rows(img: QImage) -> list[int]:
    """哪些行有内容（每 4 列采一列，够快也够准）。"""
    return [y for y in range(img.height())
            if any(img.pixelColor(x, y).alpha() > 0 for x in range(0, img.width(), 4))]


def should_put_feet_on_canvas_bottom_when_asset_follows_contract(qapp, tmp_path):
    """素材把脚放在 256 画布的 y=216、脚下留 40px 透明余量：缩到 128 后得把那段
    余量挤到画布外，让脚贴着底边——窗口底边就是贴地模式的地面（PRD v2.0 C10）。"""
    _write_band_frame(tmp_path / "pet" / "cat" / "idle", 30, 215, domain_actions.FRAME_SOURCE_SIZE)
    anim = FrameAnimator(FrameAssetLoader(tmp_path))
    anim.set_action("idle")

    rows = _opaque_rows(anim.current_frame().toImage())

    assert rows[-1] == CANVAS_SIZE - 1                 # 脚落地，不再悬空 20px
    assert rows[0] == 35                              # 顶边同步下移：缩放比例没动（93px 高）


def should_keep_centering_when_asset_is_smaller_than_contract(qapp, tmp_path):
    """尺寸不合契约的手工素材无从知道脚线在哪：保持旧的居中，不能乱推。"""
    _write_band_frame(tmp_path / "pet" / "cat" / "idle", 0, 7, 32)
    anim = FrameAnimator(FrameAssetLoader(tmp_path))
    anim.set_action("idle")

    rows = _opaque_rows(anim.current_frame().toImage())

    assert rows[0] == 0                               # 若被当契约素材推下 20px 就不是 0


def should_draw_distinct_placeholder_when_action_differs(qapp):
    renderer = PlaceholderRenderer(64)
    idle = renderer.pixmap("idle", 0)
    sleep = renderer.pixmap("sleep", 0)

    assert not idle.isNull()
    assert idle.toImage().pixelColor(32, 40) != sleep.toImage().pixelColor(32, 40)


def should_return_idle_for_unknown_state_when_mapped():
    assert action_for_state(BehaviorState.DRAGGING) == "idle"


@pytest.mark.parametrize("state,action", [
    (BehaviorState.RUNNING, "run"),
    (BehaviorState.STRETCHING, "stretch"),
])
def should_play_new_states_with_real_frames_when_mapped(animator, state, action):
    """新状态不得退化成占位脸：素材已按 10 动作重生（PRD v2.0 C11）。"""
    animator.set_state(state)

    assert animator.action == action
    assert not animator.uses_placeholder
    assert animator.interval_ms == frame_interval_ms(action)
