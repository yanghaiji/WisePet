"""「谁来了？」身份选择对话框：全家人各养各的宠物的入口体验。

- 点卡片进入：带 🔒 的身份弹出贴纸风 PIN 验证，防误入与随手翻看；
- 「＋ 新建身份」：名字 + 可选 PIN，当场创建；
- 「🔑 修改我的 PIN」：没设过直接设新的，设过先验旧 PIN（留空 = 解除）；
- 「🗑 删除身份…」（管理员）：进入删除模式——点选要删的卡片 → 输它的 PIN →
  确认后删除，不可恢复；默认身份不出现；
- 「🔑 重置家人 PIN」（管理员）：验证管理员 PIN 后清除指定家人的 PIN，
  该家人下次进入可重新设置——孩子忘 PIN 不用删号。

角色：管理员 = 默认身份（最早使用、不可删除）。诚实边界：本机 UI 约定，
防误操作，防不了绕过界面直接读文件的人。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from .input_dialog import ConfirmDialog, InputDialog

_STYLE = """
QFrame#pickerCard {
    background: #FFFDF7; border: 1px solid #DFC49A; border-radius: 16px;
}
QLabel#pickerTitle { color: #5A3E28; font-size: 18px; font-weight: 600; }
QLabel#pickerHint { color: #9A7A55; font-size: 11px; }
QPushButton#pickerClose {
    background: transparent; border: none; border-radius: 8px;
    color: #9A7A55; font-size: 19px;
}
QPushButton#pickerClose:hover { background: #F3E1C6; color: #5A3E28; }
QPushButton#profileCard {
    background: #FFFFFF; border: 1px solid #EAD8B8; border-radius: 12px;
    color: #3A2B21; padding: 10px 14px; text-align: left; font-size: 13px;
}
QPushButton#profileCard:hover { background: #F7E7CC; border-color: #E8A45C; }
QPushButton#profileCard[active="true"] { border-color: #E8A45C; background: #FDF3E0; }
QPushButton#profileCard[mode="delete"] { color: #8A4A3A; }
QPushButton#pickerAction {
    background: #F8D79B; border: 1px solid #E7C79A; border-radius: 12px;
    color: #6B4F3A; padding: 10px 14px; font-size: 13px;
}
QPushButton#pickerAction:hover { background: #F3D9A8; }
QPushButton#pickerDanger {
    background: transparent; border: 1px solid #E2CFAF; border-radius: 12px;
    color: #B0715F; padding: 10px 14px; font-size: 12px;
}
QPushButton#pickerDanger:hover { background: #F8E3DE; }
"""

#: 常用皮肤的中文展示名（未收录的显示原名）
_SKIN_LABELS = {"cat": "橘猫", "dog": "柴犬", "monkey": "小猴"}

MANAGER_NAME = ""                      # 管理员 = 默认身份（base_dir 根目录）


def change_pin_flow(parent, store, name: str) -> bool:
    """修改身份 PIN 的两步流程（picker 与设置窗共用）。

    没设过 PIN → 直接设新的；设过 → 先验旧 PIN。新 PIN 留空并确定 = 解除。
    返回是否发生了变更。
    """
    old_pin = ""
    if store.has_pin(name):
        old_dialog = InputDialog(
            f"验证「{name}」的旧 PIN", "输入当前的 PIN（4~12 位数字）：",
            password=True, placeholder="••••",
            validator=lambda text: _verify(store, name, text), parent=parent)
        old_dialog.show_centered()
        old_dialog.exec()
        if old_dialog.value is None:
            return False
        old_pin = old_dialog.value
    new_dialog = InputDialog(
        f"设置「{name}」的新 PIN", "4~12 位数字；留空并点确定 = 解除 PIN：",
        password=True, placeholder="••••（留空解除）",
        validator=lambda text: _apply_new(store, name, text, old_pin),
        parent=parent)
    new_dialog.show_centered()
    new_dialog.exec()
    return new_dialog.value is not None


def _verify(store, name: str, text: str) -> tuple[bool, str]:
    allowed, remain = store.verify_pin(name, text)
    if allowed:
        return True, ""
    if remain > 0:
        return False, f"错误次数太多，请等 {int(remain) + 1} 秒后再试。"
    return False, "PIN 不对，再想想～"


def _apply_new(store, name: str, text: str, old_pin: str) -> tuple[bool, str]:
    if not text:                          # 留空 = 解除
        return (True, "") if store.clear_pin(name, old_pin) else \
            (False, "解除失败。")
    return (True, "") if store.set_pin(name, text) else \
        (False, "PIN 只能是 4~12 位数字。")


class ProfilePickerDialog(QDialog):
    """模态身份选择；`selected` 属性为选中身份名（空串 = 默认身份）。

    mode="delete"：点选要删的卡片 → 输它的 PIN → 确认后删除并关闭；
    mode="reset"：管理员选家人的卡片 → 清除它的 PIN。
    """

    def __init__(self, store, current: str = "", *, mode: str = "select",
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._store = store
        self._current = current
        self._mode = mode
        self.selected: str | None = None     # None = 用户直接关掉（维持现状）
        titles = {"select": "谁来了？", "delete": "删除谁的身份？",
                  "reset": "重置谁的 PIN？"}
        self.setWindowTitle(f"WisePet · {titles.get(mode, '谁来了？')}")
        self.setWindowFlags(Qt.WindowType.Dialog
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.FramelessWindowHint)
        self.setModal(True)
        self.setFixedWidth(380)
        self.setStyleSheet(_STYLE)
        self._build()
        self.adjustSize()                    # 卡片数量可变：高度跟随内容

    # ---------- 界面 ----------

    def _build(self) -> None:
        card = QFrame(self)
        card.setObjectName("pickerCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

        title = QLabel({"select": "谁来了？", "delete": "删除谁的身份？",
                        "reset": "重置谁的 PIN？"}[self._mode], card)
        title.setObjectName("pickerTitle")
        hints = {
            "select": f"当前身份：{self._current or '默认身份（管理员）'}。"
                      "每人有自己的宠物与记忆，数据都在本机互不相通",
            "delete": "点击卡片 → 输入它的 PIN（若设了）→ 确认后删除。\n"
                      "该身份的宠物、记忆、聊天记录会永久删除，不可恢复。",
            "reset": "点击卡片 → 为它设置一个新 PIN（4~12 位数字），\n"
                     "然后把新 PIN 告诉家人。管理员 PIN 已在刚才验证。",
        }
        hint = QLabel(hints[self._mode], card)
        hint.setObjectName("pickerHint")
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_row.addWidget(title)
        title_row.addStretch(1)
        close_btn = QPushButton("×", self)        # 无边框窗体：给个可见的关闭入口
        close_btn.setObjectName("pickerClose")
        close_btn.setFixedSize(28, 28)
        close_btn.setToolTip("关闭（也可以按 Esc）")
        close_btn.clicked.connect(self.reject)    # 与 Esc 一致：取消，维持当前身份
        title_row.addWidget(close_btn)

        head = QVBoxLayout()
        head.setContentsMargins(18, 14, 18, 6)   # 无边框窗体：顶部多留白让标题呼吸
        head.setSpacing(4)
        head.addLayout(title_row)
        head.addWidget(hint)

        body = QVBoxLayout(card)
        body.setContentsMargins(16, 0, 16, 18)
        body.setSpacing(10)
        body.addLayout(head)
        # 卡片独立子布局：重填（删除/重置后刷新）只清这里，标题区不受影响
        self._cards_box = QVBoxLayout()
        self._cards_box.setSpacing(10)
        body.addLayout(self._cards_box)
        self._build_cards()

    def _build_cards(self) -> None:
        """填充身份卡片与操作按钮（只动卡片区，标题区永不清理）。"""
        cards = self._cards_box
        self._clear_layout(cards)

        if self._mode in ("delete", "reset"):
            for name in self._store.list():
                pet_name, skin = self._store.pet_info(name)
                lock = "🔒 " if self._store.has_pin(name) else ""
                label = {"delete": "永久删除", "reset": "重置 PIN"}[self._mode]
                button = QPushButton(
                    f"{lock}{name}\n它叫「{pet_name}」 · "
                    f"{_SKIN_LABELS.get(skin, skin)} · {label}", self)
                button.setObjectName("profileCard")
                button.setProperty("mode", self._mode)
                handler = {"delete": self._delete_flow,
                           "reset": self._reset_one}[self._mode]
                button.clicked.connect(
                    lambda _checked=False, v=name: handler(v))
                cards.addWidget(button)
            note_text = ("（默认身份是管理员，不出现在这里）"
                         if self._mode == "delete" else
                         "（管理员自己的 PIN 请用「修改我的 PIN」，不在这里重置）")
            note = QLabel(note_text, self)
            note.setObjectName("pickerHint")
            cards.addWidget(note)
            return

        self._add_card(cards, MANAGER_NAME,
                       "默认身份 👑 管理员", self._store.pet_info(""))
        for name in self._store.list():
            self._add_card(cards, name, name, self._store.pet_info(name),
                           active=(name == self._current))
        # 「沿用上次」必须显式点：Esc 是取消启动，不再隐式把人放进某个身份
        continue_btn = QPushButton(
            f"↩ 沿用上次身份：{self._current or '默认身份（管理员）'}", self)
        continue_btn.setObjectName("pickerAction")
        continue_btn.setToolTip("不切换身份，沿用上次启动的身份（设有 PIN 时仍需验证）")
        continue_btn.clicked.connect(lambda: self._choose(self._current))
        cards.addWidget(continue_btn)
        current_label = self._current or "默认身份"
        my_pin = QPushButton(f"🔑 修改「{current_label}」的 PIN…", self)
        my_pin.setObjectName("pickerAction")
        my_pin.clicked.connect(lambda: change_pin_flow(self, self._store,
                                                       self._current))
        cards.addWidget(my_pin)
        if self._store.is_manager(self._current):
            reset_btn = QPushButton("🔑 重置家人的 PIN…（孩子忘 PIN 时用）", self)
            reset_btn.setObjectName("pickerAction")
            reset_btn.clicked.connect(self._reset_family_pin)
            cards.addWidget(reset_btn)
        new_btn = QPushButton("＋ 新建身份…", self)
        new_btn.setObjectName("pickerAction")
        new_btn.clicked.connect(self._create)
        cards.addWidget(new_btn)
        manage = QPushButton("🗑 删除身份…", self)
        manage.setObjectName("pickerDanger")
        manage.clicked.connect(self._manage_delete)
        cards.addWidget(manage)

    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            elif item.layout() is not None:
                self._clear_layout(item.layout())
            layout.removeItem(item)

    def _add_card(self, layout: QVBoxLayout, value: str, title: str,
                  pet_info: tuple[str, str], active: bool = False) -> None:
        pet_name, skin = pet_info
        skin_label = _SKIN_LABELS.get(skin, skin)
        lock = "🔒 " if self._store.has_pin(value) else ""
        button = QPushButton(
            f"{lock}{title}\n它叫「{pet_name}」 · {skin_label}", self)
        button.setObjectName("profileCard")
        if active:
            button.setProperty("active", "true")
        button.clicked.connect(lambda _checked=False, v=value: self._choose(v))
        layout.addWidget(button)

    # ---------- 进入（select 模式） ----------

    def _choose(self, value: str) -> None:
        """点卡片：带 🔒 的身份先验证，通过才进入（防误入/随手翻看）。"""
        if self._store.has_pin(value) and self._prompt_pin(value) is None:
            return                              # 验证未过：留在选择页
        self.selected = value
        self.accept()

    def _prompt_pin(self, value: str) -> str | None:
        """弹 PIN 输入；通过返回 PIN 文本（供后续操作复用），未过/取消返回 None。"""
        dialog = InputDialog(
            f"进入「{value}」", "输入这个身份的 PIN（4~12 位数字）：",
            password=True, placeholder="••••",
            validator=self._pin_validator(value), parent=self)
        dialog.show_centered()
        dialog.exec()
        return dialog.value

    def _pin_validator(self, value: str):
        def validate(text: str) -> tuple[bool, str]:
            allowed, remain = self._store.verify_pin(value, text)
            if allowed:
                return True, ""
            if remain > 0:
                return False, f"错误次数太多，请等 {int(remain) + 1} 秒后再试。"
            return False, "PIN 不对，再想想～"
        return validate

    # ---------- 新建（select 模式） ----------

    def _create(self) -> None:
        name_dialog = InputDialog(
            "新建身份", "身份名（中英文/数字，如：小明）：",
            validator=self._name_validator, parent=self)
        name_dialog.show_centered()
        name_dialog.exec()
        if name_dialog.value is None:
            return                              # 取消：留在选择页
        value = name_dialog.value
        self._store.ensure(value)

        pin_dialog = InputDialog(
            f"给「{value}」设个 PIN（可选）",
            "4~12 位数字，进入这个身份时需要输入。\n"
            "留空并点确定 = 不设防。它防的是家人误入与随手翻看，"
            "不是懂电脑的人直接读文件。",
            password=True, validator=self._new_pin_validator(value),
            parent=self)
        pin_dialog.show_centered()
        pin_dialog.exec()                       # 取消/留空 = 不设 PIN
        self._choose(value)                     # 新建者自然进入自己的身份

    def _name_validator(self, text: str) -> tuple[bool, str]:
        try:
            from ..infra.profile_store import safe_name
            safe_name(text)
        except ValueError as exc:
            return False, str(exc)
        return True, ""

    def _new_pin_validator(self, value: str):
        def validate(text: str) -> tuple[bool, str]:
            if not text:                        # 留空 = 不设 PIN
                return True, ""
            return (True, "") if self._store.set_pin(value, text) else \
                (False, "PIN 只能是 4~12 位数字。")
        return validate

    # ---------- 删除（delete 模式） ----------

    def _delete_flow(self, value: str) -> None:
        pin = None
        if self._store.has_pin(value):
            pin = self._prompt_pin(value)       # 输它的 PIN（删除页复用同一验证）
            if pin is None:
                return                          # PIN 未过：留在删除页
        confirm = ConfirmDialog(
            f"永久删除「{value}」？",
            "它的宠物、记忆、聊天记录将全部删除，不可恢复。",
            danger=True, confirm_text="永久删除", parent=self)
        confirm.show_centered()
        confirm.exec()
        if not confirm.result():
            return
        if self._store.remove(value, pin or ""):
            self._flash(f"已删除「{value}」。")
            self.selected = value
            self.accept()                       # 关闭删除页（主列表随后刷新）
        else:
            self._flash("删除失败。")

    # ---------- 重置家人 PIN（reset 模式，管理员专属） ----------

    def _reset_family_pin(self) -> None:
        """管理员入口：先验证管理员自己的 PIN（没设过则引导设置），再选家人重置。"""
        if not self._store.has_pin(MANAGER_NAME):
            dialog = InputDialog(
                "设置管理员 PIN", "管理员（默认身份）还没有 PIN。\n"
                "设置一个（4~12 位数字），以后管理操作都要验证它：",
                password=True, placeholder="••••",
                validator=self._new_pin_validator(MANAGER_NAME), parent=self)
            dialog.show_centered()
            dialog.exec()
            if dialog.value is None:
                return                          # 不设 PIN 就不做管理操作
        else:
            if self._prompt_pin(MANAGER_NAME) is None:
                return                          # 管理员 PIN 验证未过
        if not self._store.list():
            self._flash("还没有家人的身份。")
            return
        self._open_sub_dialog(mode="reset")

    def _open_sub_dialog(self, mode: str) -> None:
        """打开子模式对话框：隐藏主窗口避免两个弹窗叠在同一位置。"""
        self.hide()
        try:
            sub = ProfilePickerDialog(self._store, mode=mode, parent=None)
            sub.show_centered()
            sub.exec()
            changed = sub.selected is not None
        finally:
            self.show()
            self.raise_()
        if changed:
            self._build_cards()                 # 🔒 标记 / 卡片列表刷新
            if mode == "delete":
                self._flash("身份列表已更新。")

    def _reset_one(self, value: str) -> None:
        """管理员为家人代设一个新 PIN（重置不是清空——家人直接用新 PIN 进入）。"""
        dialog = InputDialog(
            f"为「{value}」设置新 PIN",
            "输入一个 4~12 位数字的新 PIN，设置后请把这个新 PIN 告诉家人：",
            password=True, placeholder="••••",
            validator=lambda text: (True, "") if self._store.set_pin(value, text)
            else (False, "PIN 只能是 4~12 位数字。"), parent=self)
        dialog.show_centered()
        dialog.exec()
        if dialog.value is None:
            return                              # 取消：留在重置页
        self._flash(f"「{value}」的新 PIN 已设置，记得告诉家人哦。")
        self.selected = value
        self.accept()

    # ---------- 删除入口（select 模式） ----------

    def _manage_delete(self) -> None:
        if not self._store.list():
            self._flash("还没有可删除的自建身份。")
            return
        self._open_sub_dialog(mode="delete")

    def _flash(self, text: str) -> None:
        """提示卡片：先藏起当前窗口，避免提示与它叠在同一位置。"""
        self.hide()
        try:
            dialog = ConfirmDialog("WisePet", text, confirm_text="知道了",
                                   cancel_text=None, parent=None)
            dialog.show_centered()
            dialog.exec()
        finally:
            self.show()
            self.raise_()

    def show_centered(self) -> None:
        from PySide6.QtGui import QGuiApplication
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(geo.x() + (geo.width() - self.width()) // 2,
                      geo.y() + (geo.height() - self.height()) // 3)
        self.show()
        self.raise_()
