"""ui 冒烟测试共享装置：项目根导入 + 鼠标事件构造器 + 控制器替身。

offscreen 平台下窗口不会真实移动，因此鼠标交互用手搭 QMouseEvent 精确指定
「控件内坐标 + 全局坐标」，避免依赖系统光标位置（AGENT.md 规约 4：可测性优先）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QMouseEvent

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:      # 让 `import main` 可用（组装根在项目根）
    sys.path.insert(0, str(PROJECT_ROOT))

UNIT_DIR = PROJECT_ROOT / "tests" / "unit"
if str(UNIT_DIR) not in sys.path:          # 复用 app 层已有替身，避免重复造轮子
    sys.path.insert(0, str(UNIT_DIR))

ASSETS_DIR = PROJECT_ROOT / "assets"


def mouse_event(kind: QEvent.Type, local: tuple[int, int], global_: tuple[int, int],
                button=Qt.LeftButton, buttons=Qt.LeftButton) -> QMouseEvent:
    """构造带全局坐标的鼠标事件（Qt6 语义：globalPosition 决定拖动位移）。"""
    return QMouseEvent(kind, QPointF(*local), QPointF(*global_), button, buttons,
                       Qt.NoModifier)


class RecordingController:
    """PetController 替身：记录命令调用，位置/状态可预置。"""

    def __init__(self, position=(100, 100), state=None, facing=None):
        from WisePet.domain.position import Facing, PositionVO
        from WisePet.domain.state_machine import BehaviorState

        self.pet = _FakePet(PositionVO(*position),
                            state or BehaviorState.IDLE,
                            facing or Facing.RIGHT)
        self.calls: list[tuple] = []

    def on_press(self):
        self.calls.append(("press",))

    def on_drag(self, dx, dy):
        self.calls.append(("drag", dx, dy))
        self.pet.position = self.pet.position.shifted(dx, dy)

    def on_release(self):
        self.calls.append(("release",))

    def on_click(self):
        self.calls.append(("click",))

    def move_to(self, x, y):
        self.calls.append(("move_to", x, y))
        self.pet.position = self.pet.position.clamped_to((-10 ** 9, -10 ** 9, 10 ** 9, 10 ** 9))


class _FakePet:
    def __init__(self, position, state, facing):
        self.position = position
        self.state = state
        self.facing = facing


#: 128px 宠物窗口的全局矩形（QRect 右/下含端点），供聊天浮窗定位测试用
PET_RECT = QRect(400, 300, 128, 128)


@pytest.fixture
def pet_rect():
    return QRect(PET_RECT)


class FakeScreenBounds:
    """固定工作区大小的 bounds 替身（offscreen 屏幕尺寸不可控）。"""

    def __init__(self, geo=(0, 0, 800, 600)):
        self._geo = geo

    def geometry(self):
        return self._geo

    def bounds_for(self, width, height):
        x1, y1, x2, y2 = self._geo
        return (x1, y1, max(x1, x2 - width + 1), max(y1, y2 - height + 1))

    def clamp(self, x, y, width, height):
        min_x, min_y, max_x, max_y = self.bounds_for(width, height)
        return max(min_x, min(max_x, x)), max(min_y, min(max_y, y))

    def place_beside(self, pet_rect, width, height, gap=12):
        from WisePet.ui.screen_bounds import beside_position
        return beside_position(self.geometry(), pet_rect, width, height, gap)

    def watch_changes(self, callback):
        pass


@pytest.fixture
def recording_controller():
    return RecordingController()


@pytest.fixture
def fake_screen():
    return FakeScreenBounds()


@pytest.fixture
def assets_dir():
    """真实素材目录（占位帧）：组装根测试依赖它加载动画。"""
    return ASSETS_DIR
