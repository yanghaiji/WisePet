"""PetStateRepository / FrameAssetLoader / InstanceGuard / SystemClock / 平台工具 集成测试。"""
import ctypes
import logging
import sys
import time
from types import SimpleNamespace

import pytest

from WisePet.domain.actions import (ACTION_NAMES, LEGACY_ACTION_NAMES, SIMILAR_FALLBACK,
                                   STATE_TO_ACTION)
from WisePet.domain.pet import Pet
from WisePet.domain.position import PositionVO
from WisePet.domain.state_machine import BehaviorState
from WisePet.domain.vitals import VitalsVO
from WisePet.infra.frame_loader import ACTIONS, FrameAssetLoader
from WisePet.infra.instance_guard import InstanceGuard
from WisePet.infra.pet_state_repo import PetStateRepository
from WisePet.infra.platform_util import (
    NullFullscreenDetector,
    WindowsFullscreenDetector,
    create_fullscreen_detector,
    is_in_night,
    rect_covers_workarea,
    set_notification_app_id,
)
from WisePet.infra.system_clock import SystemClock


def _pet():
    return Pet(pet_id="mochi", name="小墨",
               vitals=VitalsVO(hunger=72.0, mood=85.0, energy=60.0),
               position=PositionVO(x=1200, y=980))


class TestPetStateRepository:
    def should_return_none_when_no_snapshot_yet(self, tmp_path):
        assert PetStateRepository(data_dir=tmp_path).load() is None

    def should_restore_pet_when_saved_then_loaded(self, tmp_path):
        repo = PetStateRepository(data_dir=tmp_path)
        pet = _pet()
        pet.last_updated = 1730000000.0
        repo.save(pet)
        loaded = repo.load()
        assert loaded.pet_id == "mochi"
        assert loaded.vitals == VitalsVO(72.0, 85.0, 60.0)
        assert loaded.position == PositionVO(1200, 980)
        assert loaded.last_updated == 1730000000.0

    def should_return_none_and_backup_when_state_corrupted(self, tmp_path):
        (tmp_path / "pet_state.json").write_text("{{{bad", encoding="utf-8")
        repo = PetStateRepository(data_dir=tmp_path)
        assert repo.load() is None
        assert list(tmp_path.glob("pet_state.json.bak-*"))

    def should_not_leave_temp_files_when_save(self, tmp_path):
        PetStateRepository(data_dir=tmp_path).save(_pet())
        assert [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []

    def should_return_none_when_snapshot_fields_unparsable(self, tmp_path):
        path = tmp_path / "pet_state.json"
        path.write_text('{"position": {"x": "left"}}', encoding="utf-8")
        assert PetStateRepository(data_dir=tmp_path).load() is None


class TestFrameAssetLoader:
    def _make_frames(self, root, action, names, skin="cat"):
        d = root / "assets" / "pet" / skin / action
        d.mkdir(parents=True, exist_ok=True)
        for n in names:
            (d / n).write_bytes(b"png")
        return d

    def should_load_frames_sorted_when_assets_exist(self, tmp_path):
        self._make_frames(tmp_path, "idle", ["f003.png", "f001.png", "f002.png"])
        loader = FrameAssetLoader(assets_dir=tmp_path / "assets")
        frames = loader.frames("idle")
        assert [f.name for f in frames] == ["f001.png", "f002.png", "f003.png"]

    def should_report_unavailable_when_action_dir_missing(self, tmp_path):
        loader = FrameAssetLoader(assets_dir=tmp_path / "assets")
        assert loader.is_available("walk") is False
        assert loader.frames("walk") == []

    def should_ignore_non_png_files(self, tmp_path):
        d = self._make_frames(tmp_path, "eat", ["a.png", "b.txt", "c.jpg"])
        loader = FrameAssetLoader(assets_dir=tmp_path / "assets")
        assert [f.name for f in loader.frames("eat")] == ["a.png"]

    def should_cover_all_behavior_actions(self):
        """动作清单单一真相源（PRD v2.0 C12）：infra 不得另写一份。"""
        assert ACTIONS == frozenset(ACTION_NAMES)
        assert len(ACTION_NAMES) == 10
        assert set(STATE_TO_ACTION) == set(BehaviorState)
        assert set(STATE_TO_ACTION.values()) <= ACTIONS

    def should_report_all_actions_missing_when_dir_empty(self, tmp_path):
        loader = FrameAssetLoader(assets_dir=tmp_path / "nothing")
        assert set(loader.missing_actions()) == ACTIONS

    def should_report_only_missing_actions_when_partially_installed(self, tmp_path):
        """stretch 能借到 idle 所以不算缺；run 借 walk，而 walk 也缺，仍算缺。"""
        self._make_frames(tmp_path, "idle", ["01.png"])
        loader = FrameAssetLoader(assets_dir=tmp_path / "assets")
        missing = loader.missing_actions()
        assert "idle" not in missing
        assert set(missing) == set(ACTIONS) - {"idle", "stretch"}

    def should_borrow_similar_action_when_new_action_missing(self, tmp_path, caplog):
        """v2.0 新动作在旧素材上借用相似动作，而不是退化成占位圆脸（PRD C12）。"""
        self._make_frames(tmp_path, "walk", ["01.png", "02.png"])
        loader = FrameAssetLoader(assets_dir=tmp_path / "assets")

        with caplog.at_level(logging.INFO, logger="WisePet.infra.frame_loader"):
            frames = loader.frames("run")

        assert [f.parent.name for f in frames] == ["walk", "walk"]
        assert "borrowing similar action" in caplog.text

    def should_prefer_own_frames_over_similar_borrow(self, tmp_path):
        """相似表只是兜底，本皮肤有自己的帧时绝不串用。"""
        self._make_frames(tmp_path, "walk", ["01.png"])
        self._make_frames(tmp_path, "run", ["09.png"])

        frames = FrameAssetLoader(assets_dir=tmp_path / "assets").frames("run")

        assert [f.name for f in frames] == ["09.png"]

    def should_keep_no_borrow_for_actions_without_twin(self, tmp_path):
        assert SIMILAR_FALLBACK.keys() <= set(ACTION_NAMES)
        assert "hurt" not in SIMILAR_FALLBACK
        assert FrameAssetLoader(assets_dir=tmp_path / "nothing").frames("hurt") == []

    def should_still_list_skin_when_only_legacy_actions_installed(self, tmp_path):
        """旧素材只到 8 动作：不能整皮判为未安装，否则设置窗皮肤下拉变空。"""
        for action in LEGACY_ACTION_NAMES:
            self._make_frames(tmp_path, action, ["01.png"], skin="dog")

        loader = FrameAssetLoader(assets_dir=tmp_path / "assets")

        assert loader.available_skins() == ["dog"]

    def should_read_selected_skin_dir_when_skin_given(self, tmp_path):
        """不同皮肤各自一套帧，互不串台（PRD v1.2 C3）。"""
        self._make_frames(tmp_path, "idle", ["cat01.png"], skin="cat")
        self._make_frames(tmp_path, "idle", ["dog01.png", "dog02.png"], skin="dog")

        loader = FrameAssetLoader(assets_dir=tmp_path / "assets", skin="dog")

        assert loader.skin == "dog"
        assert [f.name for f in loader.frames("idle")] == ["dog01.png", "dog02.png"]

    def should_fall_back_to_default_skin_when_selected_skin_lacks_frames(self, tmp_path):
        """选了素材不全的皮肤不能变成占位圆脸，回落到默认皮肤。"""
        self._make_frames(tmp_path, "walk", ["01.png"], skin="cat")

        loader = FrameAssetLoader(assets_dir=tmp_path / "assets", skin="monkey")

        assert [f.name for f in loader.frames("walk")] == ["01.png"]
        assert f"{loader.skin}" == "monkey"

    def should_keep_empty_when_neither_skin_has_frames(self, tmp_path):
        loader = FrameAssetLoader(assets_dir=tmp_path / "assets", skin="dog")

        assert loader.frames("idle") == []
        assert set(loader.missing_actions()) == ACTIONS

    def should_use_default_skin_when_value_unknown(self, tmp_path):
        """非法皮肤名不能拖崩加载，静默回落 cat（配置损坏恢复）。"""
        assert FrameAssetLoader(tmp_path, skin="Dragon").skin == "cat"
        assert FrameAssetLoader(tmp_path, skin=None).skin == "cat"

    def should_list_only_complete_skins_when_partial_assets(self, tmp_path):
        for action in ACTIONS:
            self._make_frames(tmp_path, action, ["01.png"], skin="cat")
        self._make_frames(tmp_path, "idle", ["01.png"], skin="dog")

        assert FrameAssetLoader(assets_dir=tmp_path / "assets").available_skins() == ["cat"]


class TestInstanceGuard:
    def should_acquire_when_port_free(self):
        guard = InstanceGuard(port=0)  # 随机端口用于测试
        assert guard.acquire() is True
        guard.release()

    def should_fail_acquire_when_already_held(self):
        first = InstanceGuard(port=0)
        first.acquire()
        second = InstanceGuard(port=first.port)
        assert second.acquire() is False
        first.release()

    def should_reacquire_after_release(self):
        guard = InstanceGuard(port=0)
        guard.acquire()
        guard.release()
        assert guard.acquire() is True

    def should_stay_acquired_when_acquire_called_twice(self):
        guard = InstanceGuard(port=0)
        guard.acquire()
        assert guard.acquire() is True
        guard.release()


class TestSystemClock:
    def should_track_wall_clock_when_now(self):
        assert SystemClock().now() == pytest.approx(time.time(), abs=2.0)

    def should_never_go_back_when_monotonic_sampled(self):
        samples = [SystemClock().monotonic() for _ in range(3)]
        assert samples == sorted(samples)


class TestPlatformUtil:
    @pytest.mark.parametrize("hour,expected", [
        (23, True), (3, True), (6, True), (7, False), (12, False), (22, False),
    ])
    def should_detect_night_window_when_wraps_midnight(self, hour, expected):
        assert is_in_night(hour, start_hour=23, end_hour=7) is expected

    @pytest.mark.parametrize("hour,expected", [
        (0, False), (1, True), (12, True), (13, False),
    ])
    def should_detect_plain_window_when_start_before_end(self, hour, expected):
        assert is_in_night(hour, start_hour=1, end_hour=13) is expected

    def should_true_when_window_rect_equals_workarea(self):
        assert rect_covers_workarea((0, 0, 1920, 1080), (0, 0, 1920, 1040)) is True

    def should_false_when_window_smaller_than_workarea(self):
        assert rect_covers_workarea((100, 100, 800, 600), (0, 0, 1920, 1040)) is False

    def should_pick_windows_detector_on_windows(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        assert isinstance(create_fullscreen_detector(), WindowsFullscreenDetector)

    def should_report_false_on_non_windows_platform(self, monkeypatch):
        """macOS MVP 暂不做全屏检测，恒返回 False（PRD 已接受降级）。"""
        monkeypatch.setattr(sys, "platform", "darwin")
        assert isinstance(create_fullscreen_detector(), NullFullscreenDetector)
        assert NullFullscreenDetector().is_user_fullscreen() is False
        assert WindowsFullscreenDetector().is_user_fullscreen() is False

    @pytest.mark.parametrize("user32", [
        # 无前台窗口
        SimpleNamespace(GetForegroundWindow=lambda: 0),
        # 拿不到窗口矩形
        SimpleNamespace(GetForegroundWindow=lambda: 1, GetWindowRect=lambda _h, _r: 0),
        # 拿不到工作区
        SimpleNamespace(GetForegroundWindow=lambda: 1, GetWindowRect=lambda _h, _r: 1,
                        SystemParametersInfoW=lambda *_a: 0),
    ])
    def should_report_false_when_win32_call_returns_no_data(self, monkeypatch, user32):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=user32), raising=False)

        assert WindowsFullscreenDetector().is_user_fullscreen() is False

    def should_report_false_when_win32_api_raises(self, monkeypatch):
        """平台 API 抛异常同样降级为 False，绝不打扰用户。"""
        monkeypatch.setattr(sys, "platform", "win32")

        def _boom(*_args):
            raise OSError("user32 unavailable")

        monkeypatch.setattr(ctypes, "windll", SimpleNamespace(
            user32=SimpleNamespace(GetForegroundWindow=_boom)), raising=False)

        assert WindowsFullscreenDetector().is_user_fullscreen() is False

    def should_skip_app_id_when_non_windows(self, monkeypatch):
        """非 Windows 无 AUMID 概念，静默返回 False。"""
        monkeypatch.setattr(sys, "platform", "darwin")
        assert set_notification_app_id("小墨") is False

    @pytest.mark.parametrize("raw", ["", "   ", None])
    def should_skip_app_id_when_blank(self, monkeypatch, raw):
        """空宠物名不去设 AUMID，免得把 tile 设成空串。"""
        monkeypatch.setattr(sys, "platform", "win32")
        assert set_notification_app_id(raw) is False

    def should_set_app_id_when_win32_succeeds(self, monkeypatch):
        """HRESULT==0 视为成功，并应把宠物名原样传给 API。"""
        monkeypatch.setattr(sys, "platform", "win32")
        calls = []

        monkeypatch.setattr(ctypes, "windll", SimpleNamespace(
            shell32=SimpleNamespace(
                SetCurrentProcessExplicitAppUserModelID=lambda v: calls.append(v) or 0)),
            raising=False)

        assert set_notification_app_id("小墨") is True
        assert calls == ["小墨"]

    @pytest.mark.parametrize("hresult", [1, -2147024891])
    def should_report_false_when_win32_returns_error(self, monkeypatch, hresult):
        """非 0 的 HRESULT 视为失败，返回 False 但不抛。"""
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(ctypes, "windll", SimpleNamespace(
            shell32=SimpleNamespace(
                SetCurrentProcessExplicitAppUserModelID=lambda _v: hresult)), raising=False)

        assert set_notification_app_id("小墨") is False

    def should_report_false_when_app_id_api_raises(self, monkeypatch):
        """shell32 不可用同样静默降级，不影响通知发出。"""
        monkeypatch.setattr(sys, "platform", "win32")

        def _boom(*_args):
            raise OSError("shell32 unavailable")

        monkeypatch.setattr(ctypes, "windll", SimpleNamespace(
            shell32=SimpleNamespace(SetCurrentProcessExplicitAppUserModelID=_boom)),
            raising=False)

        assert set_notification_app_id("小墨") is False
