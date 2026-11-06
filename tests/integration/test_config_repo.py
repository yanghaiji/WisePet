"""JsonConfigRepository 集成测试：默认值、往返、损坏恢复、脱敏。"""
import json
from pathlib import Path

import pytest

from WisePet.domain.ports import AIConfig
from WisePet.infra.config_repo import (
    JsonConfigRepository,
    CONFIG_FILE_NAME,
    DEFAULT_SYSTEM_PROMPT,
    default_data_dir,
    mask_key,
    migrate_legacy_data,
)


def _repo(tmp_path):
    return JsonConfigRepository(data_dir=tmp_path)


class TestLoadDefaults:
    def should_create_default_config_when_file_missing(self, tmp_path):
        cfg = _repo(tmp_path).load()
        assert cfg.persona.name
        assert cfg.ai.timeout_s == 30.0
        assert cfg.vitals.sad_threshold > cfg.vitals.rules.floor
        assert cfg.reminders.water.interval_min == 45
        assert cfg.reminders.sedentary.interval_min == 60

    def should_return_default_when_no_key_typed(self, tmp_path):
        cfg = _repo(tmp_path).load()
        assert cfg.ai.api_key == ""


class TestRoundtrip:
    def should_keep_values_when_save_then_load(self, tmp_path):
        repo = _repo(tmp_path)
        cfg = repo.load()
        cfg.ai = AIConfig(base_url="https://api.deepseek.com/v1",
                          api_key="sk-test-1234567890",
                          model="deepseek-chat")
        cfg.persona.system_prompt = "自定义人设 {name}"
        cfg.pet.idle_min_s = 2.0
        repo.save(cfg)
        loaded = repo.load()
        assert loaded.ai.base_url == "https://api.deepseek.com/v1"
        assert loaded.ai.api_key == "sk-test-1234567890"
        assert loaded.persona.system_prompt == "自定义人设 {name}"
        assert loaded.pet.idle_min_s == 2.0

    def should_store_file_in_data_dir_not_project(self, tmp_path):
        repo = _repo(tmp_path)
        repo.save(repo.load())
        assert (tmp_path / "config.json").exists()


class TestCorruption:
    def should_backup_and_reset_when_json_corrupted(self, tmp_path):
        (tmp_path / "config.json").write_text("not-json{{{", encoding="utf-8")
        cfg = _repo(tmp_path).load()  # 不抛异常
        assert cfg.persona.name  # 回落默认
        backups = list(tmp_path.glob("config.json.bak-*"))
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == "not-json{{{"

    def should_not_leave_temp_files_when_save(self, tmp_path):
        repo = _repo(tmp_path)
        repo.save(repo.load())
        leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []


class TestSerializationShape:
    def should_write_utf8_readable_json_when_saved(self, tmp_path):
        repo = _repo(tmp_path)
        repo.save(repo.load())
        raw = (tmp_path / "config.json").read_text(encoding="utf-8")
        data = json.loads(raw)
        assert set(data) >= {"ai", "persona", "pet", "vitals", "reminders"}
        assert "ensure_ascii" not in raw  # 中文原文保存
        assert "\\u" not in raw


class TestDataDir:
    def should_use_user_home_dir_when_not_given(self):
        """敏感配置绝不落在项目目录（规约例外项的唯一实现点）。"""
        assert default_data_dir() == Path.home() / ".WisePet"
        assert JsonConfigRepository().path == default_data_dir() / CONFIG_FILE_NAME


class TestLegacyDataMigration:
    """改名 WisePet 后不能丢 ~/.ai-bot 下已保存的 API Key 与状态快照。"""

    @staticmethod
    def _home_with_legacy(monkeypatch, tmp_path):
        home = tmp_path / "home"
        legacy = home / ".ai-bot"
        (legacy / "logs").mkdir(parents=True)
        (legacy / "config.json").write_text('{"ai": {"api_key": "sk-test"}}',
                                            encoding="utf-8")
        (legacy / "pet_state.json").write_text('{"x": 1}', encoding="utf-8")
        (legacy / "logs" / "ai-bot.log").write_text("old log", encoding="utf-8")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        return home, legacy

    def should_copy_legacy_files_when_old_dir_exists(self, tmp_path, monkeypatch):
        home, _legacy = self._home_with_legacy(monkeypatch, tmp_path)
        target = home / ".WisePet"

        migrated = migrate_legacy_data(target)

        assert (target / "config.json").read_text(encoding="utf-8") == '{"ai": {"api_key": "sk-test"}}'
        assert (target / "pet_state.json").read_text(encoding="utf-8") == '{"x": 1}'
        assert (target / "logs" / "ai-bot.log").read_text(encoding="utf-8") == "old log"
        assert {Path(p) for p in migrated} == {
            Path("config.json"), Path("pet_state.json"), Path("logs/ai-bot.log")}

    def should_keep_new_config_when_both_dirs_exist(self, tmp_path, monkeypatch):
        home, _legacy = self._home_with_legacy(monkeypatch, tmp_path)
        target = home / ".WisePet"
        target.mkdir(parents=True)
        (target / "config.json").write_text("new config", encoding="utf-8")

        migrated = migrate_legacy_data(target)

        assert (target / "config.json").read_text(encoding="utf-8") == "new config"
        assert Path("config.json") not in {Path(p) for p in migrated}

    def should_skip_when_legacy_dir_missing(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        target = home / ".WisePet"

        assert migrate_legacy_data(target) == []
        assert not target.exists()


class TestMaskKey:
    def should_keep_head_and_tail_when_key_long(self):
        masked = mask_key("sk-1234567890abcdef")
        assert masked.startswith("sk-1")
        assert masked.endswith("ef")
        assert "*" in masked
        assert "1234567890abcd" not in masked

    def should_fully_mask_when_key_short(self):
        assert mask_key("abc") == "***"

    def should_return_empty_when_key_missing(self):
        assert mask_key("") == ""


class TestV2FloorAndGaitParams:
    """v2.0 新字段（PRD C10/C11）：旧 config.json 缺键必须走默认，不能报错也不能当 False。"""

    def should_ship_v2_defaults_when_config_missing(self, tmp_path):
        pet = _repo(tmp_path).load().pet

        assert pet.stick_to_floor is True
        assert pet.fall_speed_px_per_s == 900.0
        assert pet.run_speed_px_per_s == 150.0
        assert pet.walk_max_s == 12.0
        assert (pet.idle_min_s, pet.idle_max_s) == (3.0, 9.0)
        assert pet.run_trigger_p == 0.25
        assert pet.stretch_trigger_p == 0.2

    def should_fill_defaults_when_legacy_config_lacks_new_keys(self, tmp_path):
        """v1.x 留下的文件里没有新增键：读进来仍贴地，而不是静默变成漂浮。"""
        legacy = {"pet": {"skin": "dog", "walk_speed_px_per_s": 60.0,
                          "idle_min_s": 5.0, "idle_max_s": 15.0, "max_turns": 20,
                          "click_move_threshold_px": 5}}
        (tmp_path / "config.json").write_text(json.dumps(legacy), encoding="utf-8")

        pet = _repo(tmp_path).load().pet

        assert pet.skin == "dog"
        assert pet.idle_min_s == 5.0            # 已配的值不被默认覆盖
        assert pet.stick_to_floor is True
        assert pet.fall_speed_px_per_s == 900.0
        assert pet.walk_max_s == 12.0

    def should_keep_v2_values_when_save_then_load(self, tmp_path):
        repo = _repo(tmp_path)
        cfg = repo.load()
        cfg.pet.stick_to_floor = False
        cfg.pet.fall_speed_px_per_s = 400.0
        cfg.pet.run_trigger_p = 0.5
        cfg.pet.walk_max_s = 30.0
        repo.save(cfg)

        pet = repo.load().pet

        assert pet.stick_to_floor is False      # 关掉贴地即恢复 v1.x 自由漂浮
        assert (pet.fall_speed_px_per_s, pet.walk_max_s) == (400.0, 30.0)
        assert pet.run_trigger_p == 0.5

    def should_clamp_prob_into_zero_one_when_hand_edited_json(self, tmp_path):
        """手改 json 把概率写成 25 不能“必发”，越界钳到 0~1。"""
        (tmp_path / "config.json").write_text(
            json.dumps({"pet": {"run_trigger_p": 25, "stretch_trigger_p": -3}}),
            encoding="utf-8")

        pet = _repo(tmp_path).load().pet

        assert pet.run_trigger_p == 1.0
        assert pet.stretch_trigger_p == 0.0

    @pytest.mark.parametrize("raw,expected", [
        (None, True),        # 缺键/null 不能当成 False
        (False, False),
        (True, True),
        (0, False),
        (1, True),
    ])
    def should_resolve_stick_to_floor_when_value_irregular(self, tmp_path, raw, expected):
        (tmp_path / "config.json").write_text(
            json.dumps({"pet": {"stick_to_floor": raw}}), encoding="utf-8")

        assert _repo(tmp_path).load().pet.stick_to_floor is expected

    def should_ignore_unknown_keys_when_config_from_future(self, tmp_path):
        """降级兼容：新版写出的未知键不能让解析崩，新字段仍走默认。"""
        (tmp_path / "config.json").write_text(
            json.dumps({"pet": {"gravity": "moon"}, "ui": {"theme": "dark"}}),
            encoding="utf-8")

        pet = _repo(tmp_path).load().pet

        assert pet.stick_to_floor is True


def test_default_prompt_contains_placeholders():
    assert "{name}" in DEFAULT_SYSTEM_PROMPT
    assert "{vitals_summary}" in DEFAULT_SYSTEM_PROMPT
