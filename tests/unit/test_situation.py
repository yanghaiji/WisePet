"""近期处境驱动的主动体贴回归（客观近况 + 用户说出口的情绪吐槽，不脑补）。"""
from __future__ import annotations

from WisePet.app.situation_service import SituationService
from WisePet.domain.config_model import AppConfig
from WisePet.domain.events import SpeakRequestedEvent
from WisePet.domain.situation import (
    FOLLOW_UP_GAP, REGULAR_GAP, SITUATION_KINDS, Situation, detect_situation,
    make_situation, should_care as care_due, situation_care_line,
    situation_prompt_line,
)

BASE = 1_700_000_000.0
DAY = 86400.0


# ---------- 纯逻辑 ----------

def test_detect_situation_only_explicit_first_person():
    now = 1_700_000_000.0
    s = detect_situation("最近天天加班到很晚", now)
    assert s is not None and s.kind == "overtime"
    assert s.active(now) and not s.active(s.expires_at + 1)

    assert detect_situation("感冒了，吃药休息中", now).kind == "sick"
    assert detect_situation("在备考期末，紧张", now).kind == "exam"
    # 问句/转述/玩笑不记
    assert detect_situation("你是不是很累？", now) is None
    assert detect_situation("我朋友最近加班", now) is None
    assert detect_situation("讲个加班的段子", now) is None
    assert detect_situation("今天天气不错", now) is None


def test_situation_prompt_and_care_lines():
    now = BASE
    overtime = Situation("overtime", BASE, BASE + 7 * DAY)
    prompt = situation_prompt_line(overtime, now)
    assert "加班" in prompt and "不要反复追问" in prompt and "情绪" in prompt
    assert situation_care_line(overtime, now)            # 新鲜话术
    stale = Situation("overtime", BASE - 10 * DAY, BASE + DAY)
    assert "还在加班" in situation_care_line(stale, now)  # 陈旧话术不同
    expired = Situation("overtime", BASE - 20 * DAY, BASE - DAY)
    assert situation_prompt_line(expired, now) == ""
    assert situation_care_line(expired, now) == ""


# ---------- 服务层 ----------

class _Store:
    def __init__(self):
        self.data = []

    def load(self):
        return list(self.data)

    def save(self, records):
        self.data = list(records)


class _Clock:
    def __init__(self, t=BASE):
        self.t = t

    def now(self):
        return self.t


def _bus():
    from fakes import make_collecting_bus
    return make_collecting_bus()


def test_observe_upserts_and_expires():
    store = _Store()
    clock = _Clock()
    bus, _ = _bus()
    cfg = AppConfig()
    svc = SituationService(clock, bus, store, lambda: cfg)
    svc.observe("user", "最近加班好累")
    assert len(store.data) == 1 and store.data[0]["kind"] == "overtime"
    # 同类刷新：再观察一次仍是 1 条，且 since 更新
    clock.t += 10
    svc.observe("user", "今晚还得加班")
    assert len(store.data) == 1 and store.data[0]["since"] == clock.t
    # 过期后 active 为 None
    clock.t += 8 * DAY
    assert svc.active() is None


def test_tick_cares_once_per_day_and_via_bus():
    from WisePet.domain.events import SpeakRequestedEvent
    store = _Store()
    clock = _Clock()
    bus, collected = _bus()
    cfg = AppConfig()
    svc = SituationService(clock, bus, store, lambda: cfg)
    svc.observe("user", "我感冒了在吃药")
    svc.tick(clock.now())
    speaks = [e for e in collected if isinstance(e, SpeakRequestedEvent)]
    assert len(speaks) == 1 and "不舒服" in speaks[0].text
    assert speaks[0].source == "system" and speaks[0].title == "我在呢"
    # 同一天再 tick 不重复（且过了 30s 节流）
    clock.t += 60
    svc.tick(clock.now())
    assert len([e for e in collected if isinstance(e, SpeakRequestedEvent)]) == 1
    # 次日再关心一次
    clock.t += DAY
    svc.tick(clock.now())
    assert len([e for e in collected if isinstance(e, SpeakRequestedEvent)]) == 2


def test_relearning_same_day_does_not_reset_care_dedupe():
    """真机 bug：当天再次识别同一处境，不得擦掉「今天已关怀」标记。"""
    store = _Store()
    clock = _Clock()
    bus, collected = _bus()
    cfg = AppConfig()
    svc = SituationService(clock, bus, store, lambda: cfg)
    svc.observe("user", "最近天天加班")
    svc.tick(clock.now())                      # 首次关怀
    assert len([e for e in collected if isinstance(e, SpeakRequestedEvent)]) == 1
    # 当天又学到一次（AI 双轨反复抽取 / 用户再提）→ 不应重置去重
    svc.learn_from_ai(["overtime"])
    clock.t += 60
    svc.tick(clock.now())
    assert len([e for e in collected if isinstance(e, SpeakRequestedEvent)]) == 1


def test_disabled_never_observes_or_cares():
    store = _Store()
    clock = _Clock()
    bus, collected = _bus()
    cfg = AppConfig.from_dict({"companion": {"situation_enabled": False}})
    svc = SituationService(clock, bus, store, lambda: cfg)
    svc.observe("user", "最近加班")
    assert store.data == []
    svc.tick(clock.now())
    assert collected == []


def test_make_situation_whitelist():
    s = make_situation("overtime", BASE)
    assert s is not None and s.kind == "overtime" and s.active(BASE)
    assert make_situation("MadeUp", BASE) is None
    assert "overtime" in SITUATION_KINDS and "MadeUp" not in SITUATION_KINDS
    # 情绪吐槽类别也在白名单内，且时效比客观事件短
    tired = make_situation("tired", BASE)
    assert tired is not None and tired.kind == "tired"
    assert tired.expires_at < s.expires_at
    assert {"tired", "annoyed", "stressed"} <= SITUATION_KINDS


def test_learn_from_ai_recognizes_any_wording():
    """主路径：AI 给出类别（无论原话怎么说），都记成带时效的处境。"""
    store = _Store()
    clock = _Clock()
    bus, _ = _bus()
    cfg = AppConfig()
    svc = SituationService(clock, bus, store, lambda: cfg)
    svc.learn_from_ai(["overtime", "sick", "bogus"])
    kinds = {r["kind"] for r in store.data}
    assert kinds == {"overtime", "sick"}          # 非法类别被丢弃
    assert svc.active().kind in kinds


def test_config_roundtrip():
    cfg = AppConfig.from_dict({"companion": {"situation_enabled": False}})
    assert cfg.companion.situation_enabled is False
    assert AppConfig.from_dict(cfg.to_dict()).companion.situation_enabled is False
    assert AppConfig().companion.situation_enabled is True


def test_detect_emotion_venting_only_explicit_first_person():
    """吐槽/情绪表达：只认用户明说出口的状态，不脑补、问句/转述/玩笑不算。"""
    now = BASE
    tired = detect_situation("今天真的好累，累死了", now)
    assert tired is not None and tired.kind == "tired"
    assert tired.active(now) and not tired.active(now + 3 * DAY)   # 2 天短时效
    assert detect_situation("烦死了，气死我了", now).kind == "annoyed"
    assert detect_situation("最近压力好大，有点焦虑", now).kind == "stressed"

    assert detect_situation("你是不是很累？", now) is None
    assert detect_situation("我朋友最近烦死了", now) is None
    assert detect_situation("讲个累死人的笑话", now) is None
    # 客观事件优先，不被情绪词盖过
    assert detect_situation("加班到很晚，好累", now).kind == "overtime"


def test_emotion_prompt_allows_but_does_not_dwell():
    now = BASE
    prompt = situation_prompt_line(Situation("tired", now, now + 2 * DAY), now)
    assert "共情" in prompt and "别渲染" in prompt and "好累" in prompt
    assert situation_care_line(Situation("annoyed", now, now + DAY), now)  # 有对应话术


def test_tick_cares_for_venting_and_dedupes_same_day():
    """吐槽后当天主动关心一次，重复识别不会擦掉去重标记。"""
    store = _Store()
    clock = _Clock()
    bus, collected = _bus()
    cfg = AppConfig()
    svc = SituationService(clock, bus, store, lambda: cfg)

    svc.observe("user", "烦死了，今天真的好烦")
    assert store.data[0]["kind"] == "annoyed"
    svc.tick(clock.now())
    speaks = [e for e in collected if isinstance(e, SpeakRequestedEvent)]
    assert len(speaks) == 1
    assert "糟心" in speaks[0].text
    assert speaks[0].source == "system" and speaks[0].title == "我在呢"

    svc.learn_from_ai(["annoyed"])          # 当天 AI 又识别到同一情绪
    clock.t += 60
    svc.tick(clock.now())
    assert len([e for e in collected if isinstance(e, SpeakRequestedEvent)]) == 1


def test_care_timing():
    """纯节奏：首次立即；首次后 6 小时内不问，之后问；再按约每天的间隔放慢。"""
    s0 = Situation("overtime", BASE, BASE + 7 * DAY)
    assert care_due(s0, BASE)                                 # 首次
    s1 = Situation("overtime", BASE, BASE + 7 * DAY, "", BASE, 1)
    assert not care_due(s1, BASE + FOLLOW_UP_GAP - 1)
    assert care_due(s1, BASE + FOLLOW_UP_GAP)                 # 6 小时后跟进
    s2 = Situation("overtime", BASE, BASE + 7 * DAY, "", BASE, 2)
    assert not care_due(s2, BASE + REGULAR_GAP - 1)
    assert care_due(s2, BASE + REGULAR_GAP)                   # 之后放慢
    expired = Situation("overtime", BASE, BASE - 1, "", BASE, 0)
    assert not care_due(expired, BASE)


def test_tick_follows_up_then_stops_when_expired():
    """首次贴合当下 → 6 小时后问近况 → 到期后不再追问（情绪类短时效）。"""
    store, clock = _Store(), _Clock()
    bus, collected = _bus()
    svc = SituationService(clock, bus, store, lambda: AppConfig())

    svc.observe("user", "烦死了")                # annoyed 只有 1 天时效
    svc.tick(clock.now())
    first = [e for e in collected if isinstance(e, SpeakRequestedEvent)]
    assert len(first) == 1 and "糟心" in first[0].text       # 首次话术

    clock.t += FOLLOW_UP_GAP                     # 6 小时后跟进
    svc.tick(clock.now())
    second = [e for e in collected if isinstance(e, SpeakRequestedEvent)]
    assert len(second) == 2 and "还在烦" in second[1].text   # 跟进问近况

    clock.t += DAY                               # 超过 1 天，过期清理
    svc.tick(clock.now())
    assert len([e for e in collected if isinstance(e, SpeakRequestedEvent)]) == 2
    assert store.data == []                      # 过期记录被清掉


def test_relearning_during_follow_up_keeps_sequence():
    """跟进序列中途用户又提一次，不该被打回首次、也不该立刻再弹。"""
    store, clock = _Store(), _Clock()
    bus, collected = _bus()
    svc = SituationService(clock, bus, store, lambda: AppConfig())
    svc.observe("user", "最近天天加班")
    svc.tick(clock.now())
    svc.learn_from_ai(["overtime"])              # 跟进前重新识别
    clock.t += FOLLOW_UP_GAP - 60
    svc.tick(clock.now())
    assert len([e for e in collected if isinstance(e, SpeakRequestedEvent)]) == 1
    clock.t += 60
    svc.tick(clock.now())
    speaks = [e for e in collected if isinstance(e, SpeakRequestedEvent)]
    assert len(speaks) == 2 and "还在加班" in speaks[1].text
