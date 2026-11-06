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
