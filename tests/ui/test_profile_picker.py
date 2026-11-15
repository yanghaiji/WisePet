"""本地身份的入口体验：启动选择对话框 / 宠物信息读取 / 菜单注册。

弹窗（InputPicker/PIN）是模态的：这里只测构造与无弹窗分支，
validator 级测试见 tests/unit/test_profile_pin.py。
"""
from __future__ import annotations

import json

from fakes import FakeClock
from PySide6.QtWidgets import QPushButton
from WisePet.infra.config_repo import JsonConfigRepository
from WisePet.infra.pet_state_repo import PetStateRepository
from WisePet.infra.profile_store import ProfileStore
from WisePet.ui.profile_picker import ProfilePickerDialog
from WisePet.ui.tray_menu import PetMenu

import main

_ASSETS = main.ROOT / "assets"


# ---------- ProfileStore.pet_info ----------

class TestPetInfo:
    def should_read_pet_name_and_skin_from_profile_config(self, tmp_path):
        store = ProfileStore(tmp_path)
        target = store.ensure("小明")
        (target / "config.json").write_text(json.dumps({
            "persona": {"name": "团子"}, "pet": {"skin": "dog"}}),
            encoding="utf-8")
        assert store.pet_info("小明") == ("团子", "dog")

    def should_fall_back_when_config_missing_or_broken(self, tmp_path):
        store = ProfileStore(tmp_path)
        store.ensure("小红")                       # 无 config.json
        assert store.pet_info("小红") == ("小红", "cat")
        (tmp_path / "config.json").write_text("{broken", encoding="utf-8")
        assert store.pet_info("") == ("默认身份", "cat")


# ---------- 选择对话框（构造 + 无弹窗分支） ----------

class TestPicker:
    def _store(self, tmp_path):
        store = ProfileStore(tmp_path)
        store.set_active("小明")
        store.set_active("小红")
        return store

    def _dialog(self, qtbot, tmp_path):
        dialog = ProfilePickerDialog(self._store(tmp_path), current="小明")
        qtbot.addWidget(dialog)
        return dialog

    def should_list_default_and_each_identity_with_pet_info(self, qtbot, tmp_path):
        dialog = self._dialog(qtbot, tmp_path)
        texts = [b.text() for b in dialog.findChildren(QPushButton)]
        assert any("默认身份" in t for t in texts)
        assert any("小明" in t and "新建" not in t for t in texts)
        assert any("小红" in t and "新建" not in t for t in texts)
        assert any("＋ 新建身份" in t for t in texts)
        assert any("删除身份" in t for t in texts)

    def should_choose_and_accept(self, qtbot, tmp_path):
        dialog = self._dialog(qtbot, tmp_path)
        dialog._choose("小红")
        assert dialog.selected == "小红" and dialog.result() == 1

    def should_have_explicit_continue_button(self, qtbot, tmp_path):
        """Esc 不再是隐式「按上次继续」：延续上次必须点这个按钮。"""
        dialog = self._dialog(qtbot, tmp_path)     # current="小明"
        texts = [b.text() for b in dialog.findChildren(QPushButton)]
        assert any("沿用上次身份：小明" in t for t in texts)


# ---------- 启动身份选择（Esc 取消，不隐式落到管理员） ----------

class _StubPicker:
    """替身：模拟选择页的返回码与选中身份。"""
    def __init__(self, result, selected):
        self._result = result
        self.selected = selected

    def __call__(self, *_a, **_kw):
        return self

    def exec(self):
        return self._result


class TestStartupProfileSelection:
    @staticmethod
    def _store(tmp_path):
        store = ProfileStore(tmp_path)
        store.ensure("小明")
        store.set_active("小明")
        return store

    def should_cancel_startup_when_picker_rejected(self, tmp_path, monkeypatch):
        """真机 bug：Esc 后不能静默以管理员身份进程序。"""
        store = self._store(tmp_path)
        monkeypatch.setattr(main, "ProfilePickerDialog",
                            _StubPicker(0, None))       # QDialog.Rejected
        assert main.select_startup_profile(store, "") is None

    def should_use_explicit_selection_and_remember_it(self, tmp_path,
                                                      monkeypatch):
        store = self._store(tmp_path)
        monkeypatch.setattr(main, "ProfilePickerDialog",
                            _StubPicker(1, "小红"))
        assert main.select_startup_profile(store, "") == "小红"
        assert store.active() == "小红"

    def should_skip_picker_when_profile_arg_given(self, tmp_path, monkeypatch):
        store = self._store(tmp_path)
        monkeypatch.setattr(main, "ProfilePickerDialog",
                            _StubPicker(0, None))
        assert main.select_startup_profile(store, "小明") == "小明"

    def should_enter_default_without_picker_for_fresh_user(self, tmp_path,
                                                           monkeypatch):
        store = ProfileStore(tmp_path)                # 无自建身份、无 active
        monkeypatch.setattr(main, "ProfilePickerDialog",
                            _StubPicker(0, None))
        assert main.select_startup_profile(store, "") == ""

    def should_register_actions_in_menu(qtbot):
        pass                                       # 见下方函数式用例


# ---------- 菜单与切换 ----------

def test_pet_menu_registers_profile_action(qtbot):
    from PySide6.QtWidgets import QWidget
    host = QWidget()
    qtbot.addWidget(host)
    menu = PetMenu(on_settings=lambda: None, on_feed=lambda: None,
                   on_pet_head=lambda: None, on_toggle_visible=lambda: None,
                   on_quit=lambda: None, parent=host, on_profile=lambda: None)
    assert menu.act_profile.text().startswith("切换身份")


def test_switch_profile_relaunches_new_process(qapp, tmp_path, monkeypatch):
    """热切换：拉起新进程（带 --profile）而不是要求手动重启。"""
    import subprocess
    launched = []
    monkeypatch.setattr(main, "default_data_dir", lambda: tmp_path)

    class FakePicker:
        selected = "小明"

        def __init__(self, *_a, **_kw):
            pass

        def exec(self):
            return 1

    monkeypatch.setattr(main, "ProfilePickerDialog", FakePicker)
    monkeypatch.setattr(main.QProcess, "startDetached",
                        staticmethod(lambda program, args, cwd=None:
                                     launched.append((program, args))))
    monkeypatch.setattr(main.QTimer, "singleShot",
                        staticmethod(lambda *_a, **_kw: None))
    pet_app = main.PetApplication(
        qapp, config_repo=JsonConfigRepository(tmp_path / "default"),
        state_repo=PetStateRepository(tmp_path / "default"),
        assets_dir=_ASSETS, clock=FakeClock(), tray_available=True)
    try:
        # 气泡/TTS 链不真实触发：防止 hide_timer 在 shutdown 后跨测试污染
        monkeypatch.setattr(pet_app._controller, "speak",
                            lambda *_a, **_kw: None)
        pet_app._switch_profile()
        assert launched and "--profile=小明" in launched[0][1]
        assert ProfileStore(tmp_path).active() == "小明"
    finally:
        pet_app.shutdown()


def test_command_registry_has_profile_and_family_commands():
    ids = [entry[0] for entry in main.COMMANDS]
    assert "profile_switch" in ids and "family_share" in ids
