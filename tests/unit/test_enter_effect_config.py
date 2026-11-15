"""enter_effect 配置项：宽松解析，非法值回落 auto，合法值原样保留。"""
from WisePet.domain.config_model import AppConfig


def _enter_effect_of(data: dict) -> str:
    return AppConfig.from_dict(data).pet.enter_effect


def should_default_to_auto():
    assert _enter_effect_of({}) == "auto"


def should_accept_valid_kinds():
    for kind in ("spawn", "random", "burst", "puzzle", "terminal",
                "curtain", "ink", "magnifier"):
        assert _enter_effect_of({"pet": {"enter_effect": kind}}) == kind


def should_fallback_on_invalid_kind():
    assert _enter_effect_of({"pet": {"enter_effect": "explode"}}) == "auto"
    assert _enter_effect_of({"pet": {"enter_effect": 123}}) == "auto"
