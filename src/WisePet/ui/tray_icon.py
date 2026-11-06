"""系统托盘入口：宠物藏起来之后仍能把它找回来（FR v1.11 C21）。

宠物窗是无边框 Tool 窗，右键菜单挂在它身上；一旦 `setVisible(False)`，
屏幕上就没有任何可右键的地方，菜单里那句「显示宠物」根本点不到。
系统托盘是进程没有可见窗口时唯一还在的入口。
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

TOOLTIP_VISIBLE = "WisePet · 右键菜单可操作，双击图标隐藏"
TOOLTIP_HIDDEN = "WisePet（已隐藏）· 点托盘图标或「显示宠物」找回"
#: 托盘不可用时不能再让用户把唯一入口藏掉
HIDE_DISABLED_HINT = "当前系统没有可用托盘，禁用隐藏以免找不到宠物"


class PetTrayIcon(QSystemTrayIcon):
    """常驻系统托盘：复用右键菜单，单击/双击切换宠物可见性。"""

    def __init__(self, menu: QMenu, icon: QPixmap,
                 on_activate: Callable[[], None],
                 parent: QWidget | None = None):
        super().__init__(QIcon(icon), parent)
        self._on_activate = on_activate
        self.setContextMenu(menu)
        self.setToolTip(TOOLTIP_VISIBLE)
        self.activated.connect(self._on_activated)
        self.show()

    @staticmethod
    def is_available() -> bool:
        """平台是否真的提供系统托盘（offscreen 下为 False，用于降级判定）。"""
        return QSystemTrayIcon.isSystemTrayAvailable()

    def set_hidden_state(self, hidden: bool) -> None:
        self.setToolTip(TOOLTIP_HIDDEN if hidden else TOOLTIP_VISIBLE)

    def _on_activated(self, reason) -> None:
        # 右键（Context）留给托盘菜单，其余点击都当作「把宠物找回来 / 收起来」
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self._on_activate()
