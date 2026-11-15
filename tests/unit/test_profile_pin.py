"""身份 PIN 验证：scrypt 哈希 / 错误锁定 / 选择对话框集成。"""
from __future__ import annotations

import json
import time

import pytest
from PySide6.QtWidgets import QPushButton, QInputDialog, QMessageBox
from fakes import FakeClock
from WisePet.infra.profile_store import ProfileStore


class TestPinStore:
    def _store(self, tmp_path):
        return ProfileStore(tmp_path)

    def should_set_and_verify_pin(self, tmp_path):
        store = self._store(tmp_path)
        store.ensure("小明")
        assert store.set_pin("小明", "123456") is True
        assert store.has_pin("小明") is True
        assert store.verify_pin("小明", "123456") == (True, 0.0)
        assert store.verify_pin("小明", "000000")[0] is False

    def should_reject_invalid_pin_format(self, tmp_path):
        store = self._store(tmp_path)
        store.ensure("小明")
        for bad in ("123", "1234567890123", "abcd", "", "12 34"):
            assert store.set_pin("小明", bad) is False
        assert store.has_pin("小明") is False   # 非法 PIN 从未落盘

    def should_never_store_plain_pin(self, tmp_path):
        store = self._store(tmp_path)
        store.ensure("小明")
        store.set_pin("小明", "123456")
        raw = (tmp_path / "profiles" / "小明" / "pin.json").read_text(
            encoding="utf-8")
        assert "123456" not in raw and "hash" in raw

    def should_lock_after_three_failures_even_across_restart(self, tmp_path):
        store = self._store(tmp_path)
        store.ensure("小明")
        store.set_pin("小明", "123456")
        for _ in range(3):
            assert store.verify_pin("小明", "000000")[0] is False
        ok, remain = store.verify_pin("小明", "123456")   # 连对的 PIN 也被冷却挡住
        assert ok is False and remain > 0
        # 重启（新实例读同一份 pin.json）后冷却仍然生效
        ok, remain = ProfileStore(tmp_path).verify_pin("小明", "123456")
        assert ok is False and remain > 0

    def should_reset_failures_on_success(self, tmp_path):
        store = self._store(tmp_path)
        store.ensure("小明")
        store.set_pin("小明", "123456")
        store.verify_pin("小明", "000000")
        store.verify_pin("小明", "000000")
        assert store.verify_pin("小明", "123456") == (True, 0.0)  # 未达 3 次不锁
        state = json.loads((tmp_path / "profiles" / "小明" / "pin.json")
                           .read_text(encoding="utf-8"))
        assert state["failed"] == 0                 # 成功后清零

    def should_clear_pin_only_with_correct_old_pin(self, tmp_path):
        store = self._store(tmp_path)
        store.ensure("小明")
        store.set_pin("小明", "123456")
        assert store.clear_pin("小明", "000000") is False
        assert store.has_pin("小明") is True        # 错的旧 PIN 解不掉
        assert store.clear_pin("小明", "123456") is True
        assert store.has_pin("小明") is False
        assert store.verify_pin("小明", "") == (True, 0.0)  # 解除后不再验证

    def should_not_verify_unknown_identity(self, tmp_path):
        assert self._store(tmp_path).verify_pin("不存在", "123456") == (True, 0.0)


# ---------- 选择对话框集成（弹窗为模态：只测纯逻辑层） ----------

class TestPickerPinFlow:
    def _store_with_pin(self, tmp_path):
        store = ProfileStore(tmp_path)
        store.set_active("小明")
        store.ensure("小明")
        store.set_pin("小明", "123456")
        return store

    def _dialog(self, qtbot, store):
        from WisePet.ui.profile_picker import ProfilePickerDialog
        dialog = ProfilePickerDialog(store, current="")
        qtbot.addWidget(dialog)
        return dialog

    def should_show_lock_mark_on_card(self, qtbot, tmp_path):
        dialog = self._dialog(qtbot, self._store_with_pin(tmp_path))
        texts = [b.text() for b in dialog.findChildren(QPushButton)]
        assert any("🔒" in t and "小明" in t for t in texts)

    def should_enter_when_pin_validation_passes(self, qtbot, tmp_path,
                                                monkeypatch):
        dialog = self._dialog(qtbot, self._store_with_pin(tmp_path))
        monkeypatch.setattr(dialog, "_prompt_pin", lambda _value: "123456")
        dialog._choose("小明")
        assert dialog.selected == "小明"

    def should_stay_when_pin_validation_fails(self, qtbot, tmp_path,
                                              monkeypatch):
        dialog = self._dialog(qtbot, self._store_with_pin(tmp_path))
        monkeypatch.setattr(dialog, "_prompt_pin", lambda _value: None)
        dialog._choose("小明")
        assert dialog.selected is None              # 验证未过留在选择页

    def should_validate_pin_against_store(self, qtbot, tmp_path):
        dialog = self._dialog(qtbot, self._store_with_pin(tmp_path))
        validator = dialog._pin_validator("小明")
        assert validator("123456") == (True, "")
        ok, message = validator("000000")
        assert ok is False and message              # 错误信息进卡片红字条

    def should_delete_mode_list_only_custom_identities(self, qtbot, tmp_path):
        store = self._store_with_pin(tmp_path)
        from WisePet.ui.profile_picker import ProfilePickerDialog
        dialog = ProfilePickerDialog(store, mode="delete")
        qtbot.addWidget(dialog)
        texts = [b.text() for b in dialog.findChildren(QPushButton)]
        assert any("小明" in t for t in texts)        # 只列自建身份
        assert not any("默认身份" in t for t in texts)  # 默认身份不可删
        assert not any("新建" in t for t in texts)

    def should_delete_flow_accept_after_pin_and_confirm(self, qtbot, tmp_path,
                                                        monkeypatch):
        import WisePet.ui.profile_picker as pp
        store = self._store_with_pin(tmp_path)
        dialog = self._dialog(qtbot, store)
        monkeypatch.setattr(dialog, "_prompt_pin", lambda _value: "123456")

        class FakeConfirm:
            def __init__(self, *_a, **_kw):
                pass

            def show_centered(self):
                pass

            def exec(self):
                pass

            def result(self):
                return 1

        monkeypatch.setattr(pp, "ConfirmDialog", FakeConfirm)
        dialog._delete_flow("小明")
        assert dialog.selected == "小明" and dialog.result() == 1
        assert not (tmp_path / "profiles" / "小明").exists()

    def should_delete_flow_stay_when_pin_fails(self, qtbot, tmp_path,
                                               monkeypatch):
        store = self._store_with_pin(tmp_path)
        dialog = self._dialog(qtbot, store)
        monkeypatch.setattr(dialog, "_prompt_pin", lambda _value: None)
        dialog._delete_flow("小明")
        assert dialog.selected is None               # PIN 未过：不删
        assert (tmp_path / "profiles" / "小明").is_dir()

    def should_remove_identity_only_with_correct_pin(self, tmp_path):
        store = self._store_with_pin(tmp_path)
        assert store.remove("小明", "000000") is False
        assert (tmp_path / "profiles" / "小明").is_dir()
        assert store.remove("小明", "123456") is True
        assert not (tmp_path / "profiles" / "小明").exists()
        assert store.active() == ""                 # active 自动清理

    def should_never_remove_default_identity(self, tmp_path):
        store = ProfileStore(tmp_path)
        assert store.remove("") is False
        assert store.remove("不存在") is False
