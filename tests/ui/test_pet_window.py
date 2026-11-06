"""PetWindow 冒烟：窗口属性、拖拽/点击判定、位置同步（PRD US-1/US-2）。"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QContextMenuEvent

from WisePet.domain.config_model import AppConfig
from WisePet.infra.frame_loader import FrameAssetLoader
from WisePet.ui.frame_animator import FrameAnimator
from WisePet.ui.pet_window import PetWindow
from conftest import ASSETS_DIR, mouse_event


@pytest.fixture
def window(qtbot, recording_controller, fake_screen):
    animator = FrameAnimator(FrameAssetLoader(ASSETS_DIR))
    win = PetWindow(recording_controller, animator, lambda: AppConfig(), fake_screen,
                    size=64)
    qtbot.addWidget(win)
    win.sync_from_pet()
    return win, recording_controller


def should_be_frameless_topmost_translucent_when_built(window):
    win, _ = window
    flags = win.windowFlags()
    assert flags & Qt.FramelessWindowHint
    assert flags & Qt.WindowStaysOnTopHint
    assert win.testAttribute(Qt.WA_TranslucentBackground)
    # 防真机闪白：无边框投影会被渲染成白边、系统背景擦除会先铺白，两者都要关掉
    assert flags & Qt.NoDropShadowWindowHint
    assert win.testAttribute(Qt.WA_NoSystemBackground)


def should_leave_transparent_pixels_outside_the_cat(window):
    """画完后猫以外必须是全透明，绝不能残留白底（真机闪白的根因）。

    直接 grab 出窗口位图，查四角 alpha：只要 paintEvent 把画布先刷成透明，
    四角就一定是 0；一旦哪天又退回系统擦白或没清画布，这里会立刻红。
    """
    win, _ = window
    win.sync_from_pet()
    img = win.grab().toImage()
    w, h = img.width(), img.height()
    # 顶部两角在猫头顶上方，必须透明（画布被刷成透明而非铺白）
    assert img.pixelColor(0, 0).alpha() == 0
    assert img.pixelColor(w - 1, 0).alpha() == 0
    # 整幅要有相当比例的全透明像素：证明背景是透明而不是被填成白底
    trans = sum(1 for y in range(h) for x in range(w) if img.pixelColor(x, y).alpha() == 0)
    assert trans > w * h * 0.1


def should_emit_clicked_and_skip_drag_when_mouse_not_moved(window, qtbot):
    """原地按下松开：不产生位移命令，且算一次点击（点击唤出输入条）。"""
    win, controller = window
    win.mousePressEvent(mouse_event(QEvent.MouseButtonPress, (10, 10), (110, 110)))
    with qtbot.waitSignal(win.clicked, timeout=200):
        win.mouseReleaseEvent(mouse_event(QEvent.MouseButtonRelease, (11, 11), (111, 111),
                                          buttons=Qt.NoButton))

    assert ("press",) in controller.calls
    assert ("release",) in controller.calls
    assert not [c for c in controller.calls if c[0] == "drag"]


def should_forward_incremental_deltas_when_moved_beyond_threshold(window):
    """超过点击阈值后逐段将位移交给 controller（PRD US-2）。"""
    win, controller = window
    win.mousePressEvent(mouse_event(QEvent.MouseButtonPress, (10, 10), (110, 110)))
    win.mouseMoveEvent(mouse_event(QEvent.MouseMove, (20, 18), (120, 118)))
    win.mouseMoveEvent(mouse_event(QEvent.MouseMove, (34, 30), (134, 130)))
    win.mouseReleaseEvent(mouse_event(QEvent.MouseButtonRelease, (34, 30), (134, 130),
                                      buttons=Qt.NoButton))

    assert [c for c in controller.calls if c[0] == "drag"] == [
        ("drag", 10, 8), ("drag", 14, 12)]
    assert ("release",) in controller.calls


def should_ignore_small_movement_as_click_when_within_threshold(window):
    win, controller = window
    win.mousePressEvent(mouse_event(QEvent.MouseButtonPress, (10, 10), (110, 110)))
    win.mouseMoveEvent(mouse_event(QEvent.MouseMove, (12, 12), (112, 112)))
    win.mouseReleaseEvent(mouse_event(QEvent.MouseButtonRelease, (12, 12), (112, 112),
                                      buttons=Qt.NoButton))

    assert not [c for c in controller.calls if c[0] == "drag"]


def should_follow_pet_position_when_sync(window):
    win, controller = window
    controller.pet.position = controller.pet.position.shifted(50, -20)
    win.sync_from_pet()

    assert (win.x(), win.y()) == (controller.pet.position.x, controller.pet.position.y)


def should_emit_context_request_when_right_click(window):
    win, _ = window
    received = []
    win.context_requested.connect(received.append)
    win.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Other,
                                           QPoint(10, 10), QPoint(110, 110)))

    assert received and received[0] == QPoint(110, 110)


def should_anchor_bubble_above_head_center(window):
    win, _ = window
    anchor = win.bubble_anchor()
    geometry = win.frameGeometry()

    assert anchor.x() == geometry.center().x()
    assert anchor.y() == geometry.top() - 4


def should_emit_moved_only_when_pet_position_changes(window):
    """FR v1.10 C18：位置没变不惊动聊天浮窗，变了才通知重新贴边。"""
    win, controller = window
    received = []
    win.moved.connect(lambda: received.append(1))

    win.sync_from_pet()
    assert received == []

    controller.pet.position = controller.pet.position.shifted(30, 10)
    win.sync_from_pet()

    assert received == [1]


def should_report_window_rect_from_domain_position(window):
    win, controller = window
    rect = win.window_rect()

    assert (rect.left(), rect.top()) == (controller.pet.position.x,
                                        controller.pet.position.y)
    assert (rect.width(), rect.height()) == (win.width(), win.height())
