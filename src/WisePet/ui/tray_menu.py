"""右键菜单：设置 / 投喂 / 抚摸 / 历史对话 / 重载素材 / 隐藏 / 退出（PRD US-1/US-4/US-6）。"""
from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtWidgets import QMenu, QWidget

from ..domain.product import scene_description, scene_ids, scene_label

logger = logging.getLogger(__name__)


class PetMenu(QMenu):
    """宠物右键菜单；动作全部走回调，便于 offscreen 冒烟测试断言。"""

    def __init__(self, on_settings: Callable[[], None], on_feed: Callable[[], None],
                 on_pet_head: Callable[[], None], on_toggle_visible: Callable[[], None],
                 on_quit: Callable[[], None], parent: QWidget | None = None,
                 on_reload: Callable[[], None] | None = None,
                 on_history: Callable[[], None] | None = None,
                 on_focus: Callable[[], None] | None = None,
                 on_stats: Callable[[], None] | None = None,
                 on_screenshot: Callable[[], None] | None = None,
                 on_scene: Callable[[str], None] | None = None,
                 on_toggle_mute: Callable[[], None] | None = None,
                 on_undo: Callable[[], None] | None = None,
                 on_onboarding: Callable[[], None] | None = None,
                 on_palette: Callable[[], None] | None = None):
        super().__init__(parent)
        self.setStyleSheet(
            "QMenu{background:#FFFDF7;border:1px solid #E2CFAF;border-radius:10px;"
            "padding:6px;color:#3A2B21;}"
            "QMenu::item{padding:7px 22px;border-radius:6px;}"
            "QMenu::item:selected{background:#F7E7CC;color:#6B4F3A;}"
            "QMenu::separator{height:1px;background:#EDE0C8;margin:5px 8px;}")
        self.act_settings = self.addAction("设置…")
        self.act_feed = self.addAction("投喂")
        self.act_pet_head = self.addAction("抚摸")
        self.addSeparator()
        # 历史对话不随点击宠物弹出，只从右键进来（FR v1.9 C15）
        self.act_history = self.addAction("历史对话…")
        self.act_screenshot = self.addAction("截图问小墨")
        self.act_focus = self.addAction("番茄钟 25 分钟")
        self.act_stats = self.addAction("陪伴统计…")
        # 帧图按路径缓存在内存里，换素材后不重启看不到新图，所以留一个重载入口
        self.act_reload = self.addAction("重新加载素材")
        self.act_palette = self.addAction("命令面板…（搜所有能力）")
        self.status_action = self.addAction("")
        self.status_action.setEnabled(False)     # 常驻状态说明，不可点
        self.addSeparator()
        # P1/P2：一键场景包 / 免打扰 / 撤销写盘 / 重看引导
        self.scenes_menu = self.addMenu("一键场景包")
        self.scenes_menu.setEnabled(on_scene is not None)
        self.scene_actions = {}
        for scene in scene_ids():
            action = self.scenes_menu.addAction(scene_label(scene))
            action.setToolTip(scene_description(scene))
            if on_scene is not None:
                action.triggered.connect(
                    lambda _checked=False, s=scene: on_scene(s))
            self.scene_actions[scene] = action
        self.act_mute = self.addAction("免打扰：关")
        self.act_mute.setCheckable(True)
        self.act_undo = self.addAction("撤销上一次写文件")
        self.act_undo.setEnabled(False)
        self.act_onboarding = self.addAction("再看一次新手引导")
        self.addSeparator()
        self.act_toggle = self.addAction("隐藏宠物")
        self.act_quit = self.addAction("退出")

        self.act_settings.triggered.connect(on_settings)
        self.act_feed.triggered.connect(on_feed)
        self.act_pet_head.triggered.connect(on_pet_head)
        self.act_toggle.triggered.connect(on_toggle_visible)
        self.act_quit.triggered.connect(on_quit)
        if on_history is not None:
            self.act_history.triggered.connect(on_history)
        else:
            self.act_history.setEnabled(False)
        if on_reload is not None:
            self.act_reload.triggered.connect(on_reload)
        else:
            self.act_reload.setEnabled(False)
        for action, callback in ((self.act_focus, on_focus),
                                 (self.act_stats, on_stats),
                                 (self.act_screenshot, on_screenshot),
                                 (self.act_mute, on_toggle_mute),
                                 (self.act_undo, on_undo),
                                 (self.act_onboarding, on_onboarding),
                                 (self.act_palette, on_palette)):
            if callback is not None:
                action.triggered.connect(callback)
            else:
                action.setEnabled(False)

    def set_hidden_state(self, hidden: bool) -> None:
        """菜单文案跟随当前可见性（「隐藏/显示宠物」）。"""
        self.act_toggle.setText("显示宠物" if hidden else "隐藏宠物")

    def set_muted(self, muted: bool) -> None:
        """免打扰开关的勾选与文案同步（不触发 triggered，避免回调自激）。"""
        self.act_mute.blockSignals(True)
        self.act_mute.setChecked(muted)
        self.act_mute.setText(f"免打扰：{'开' if muted else '关'}")
        self.act_mute.blockSignals(False)

    def set_undo_enabled(self, enabled: bool) -> None:
        self.act_undo.setEnabled(enabled)

    def set_companion_status(self, text: str) -> None:
        """常驻显示「为什么安静/额度用完没」，不用等出问题才解释。"""
        self.status_action.setText(text)
