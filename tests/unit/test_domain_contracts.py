"""领域契约异常路径：非法入参即拒绝，配置解析坏值即回落（不变量 / PRD 可靠性）。"""
import pytest

from WisePet.domain.chat_session import ChatSession
from WisePet.domain.config_model import AppConfig
from WisePet.domain.errors import AIGatewayError, IllegalTransitionError
from WisePet.domain.events import SpeakRequestedEvent
from WisePet.domain.state_machine import BehaviorState, BehaviorStateMachine
from WisePet.domain.vitals import VitalsVO


class TestEventAndErrorContracts:
    def should_reject_when_speak_text_blank(self):
        with pytest.raises(ValueError):
            SpeakRequestedEvent(text="   ")

    def should_reject_when_gateway_kind_unknown(self):
        with pytest.raises(ValueError):
            AIGatewayError("mystery", "boom")


class TestSessionContract:
    def should_reject_when_max_turns_below_one(self):
        with pytest.raises(ValueError):
            ChatSession(max_turns=0)


class TestStateMachineContract:
    def should_refuse_transition_when_target_is_current(self):
        machine = BehaviorStateMachine()

        assert machine.can_transition(BehaviorState.IDLE) is False
        with pytest.raises(IllegalTransitionError):
            machine.transition(BehaviorState.IDLE)


class TestVitalsContract:
    def should_raise_energy_when_rested(self):
        assert VitalsVO(80.0, 80.0, 50.0).rested(30.0).energy == 80.0


class TestConfigParsing:
    def should_read_sleep_recovery_when_rules_provide_it(self):
        """睡着回血的速度要能从 json 里改：只认默认值的话，用户调不了「睡多久算饱」。"""
        cfg = AppConfig.from_dict({"vitals": {"rules": {"energy_recover_per_hour": 12}}})

        assert cfg.vitals.rules.energy_recover_per_hour == 12.0

    def should_fall_back_to_defaults_when_numbers_unparsable(self):
        defaults = AppConfig()

        cfg = AppConfig.from_dict({"ai": {"temperature": "hot", "timeout_s": None},
                                   "pet": {"max_turns": "many", "idle_min_s": ""}})

        assert cfg.ai.temperature == defaults.ai.temperature
        assert cfg.ai.timeout_s == defaults.ai.timeout_s
        assert cfg.pet.max_turns == defaults.pet.max_turns
        assert cfg.pet.idle_min_s == defaults.pet.idle_min_s

    def should_fall_back_to_defaults_when_payload_not_mapping(self):
        assert AppConfig.from_dict(None).persona.name == AppConfig().persona.name

    def should_default_and_clamp_chat_idle_hide_seconds(self):
        """聊天框空闲隐藏时长：默认 30s，可从 json 改，负值钳 0、非法回默认。"""
        assert AppConfig().chat.idle_hide_s == 30.0
        assert AppConfig.from_dict({"chat": {"idle_hide_s": 10}}).chat.idle_hide_s == 10.0
        assert AppConfig.from_dict({"chat": {"idle_hide_s": -5}}).chat.idle_hide_s == 0.0
        assert AppConfig.from_dict({"chat": {"idle_hide_s": "x"}}).chat.idle_hide_s == 30.0

    def should_default_companion_sections(self):
        """v2.2 四节默认值：TTS 关、系统通知关、周报开（周日 20 点）、成长开。"""
        cfg = AppConfig()
        assert (cfg.tts.enabled, cfg.tts.volume, cfg.tts.rate) == (False, 100, 0)
        assert cfg.notification.use_system is False
        assert (cfg.weekly.enabled, cfg.weekly.weekday, cfg.weekly.hour) == (True, 6, 20)
        assert cfg.growth.enabled is True

    def should_override_and_clamp_companion_sections(self):
        """json 覆盖 + 越界钳制 + 非法回默认（音量 0~100、语速 -10~10、周/时取模）。"""
        cfg = AppConfig.from_dict({
            "tts": {"enabled": True, "volume": 150, "rate": -99},
            "notification": {"use_system": True},
            "weekly": {"enabled": False, "weekday": 9, "hour": 30},
            "growth": {"enabled": False},
        })
        assert cfg.tts.enabled is True
        assert cfg.tts.volume == 100          # 150 钳到上限
        assert cfg.tts.rate == -10            # -99 钳到下限
        assert cfg.notification.use_system is True
        assert cfg.weekly.enabled is False
        assert cfg.weekly.weekday == 2        # 9 % 7
        assert cfg.weekly.hour == 6           # 30 % 24
        assert cfg.growth.enabled is False

    def should_fall_back_when_companion_numbers_unparsable(self):
        defaults = AppConfig()
        cfg = AppConfig.from_dict({"tts": {"volume": "loud", "rate": None},
                                   "weekly": {"weekday": "x"}})
        assert cfg.tts.volume == defaults.tts.volume
        assert cfg.tts.rate == defaults.tts.rate
        assert cfg.weekly.weekday == defaults.weekly.weekday

    def should_roundtrip_companion_sections_via_to_dict(self):
        cfg = AppConfig.from_dict({"tts": {"enabled": True, "volume": 40}})
        again = AppConfig.from_dict(cfg.to_dict())
        assert again.tts.enabled is True and again.tts.volume == 40
