"""LocalResponder：未配 AI Key 时的本地回复引擎（离线也要能聊天，v1.3 小脑）。"""
import pytest

from WisePet.app import corpus
from WisePet.app.local_responder import (
    LOCAL_MODE_HINT,
    SPECIES_LABEL,
    SPECIES_SOUND,
    LocalContext,
    LocalResponder,
)


def make_responder(**values):
    ctx = LocalContext(**values)
    return LocalResponder(lambda: ctx)


class TestRuleRouting:
    @pytest.mark.parametrize(("text", "rule"), [
        ("你好呀", "greet"),
        ("hi 在忙吗", "greet"),
        ("hello", "greet"),
        ("你是谁", "who"),
        ("你叫什么名字", "who"),
        ("今天心情怎么样", "feel"),
        ("饿不饿", "food"),
        ("困了吗", "sleep"),
        ("小猫咪真可爱", "praise"),
        ("笨蛋", "insult"),
        ("抱抱你", "love"),
        ("讲个笑话", "joke"),
        ("现在几点了", "time"),
        ("今天天气如何", "weather"),
        ("你能做什么", "help"),
        ("记得提醒我喝水", "remind"),
        ("怎么配置 api key", "ai"),
        ("谢谢你", "thanks"),
        ("我下班了，拜拜", "bye"),
        ("随便说点什么奇怪的", "story"),
        ("这两个我该怎么选", "advice"),
        ("压力好大", "stress"),
    ])
    def should_route_to_expected_rule_when_keyword_hit(self, text, rule):
        assert make_responder().matched_rule(text) == rule

    @pytest.mark.parametrize("text", ["this is a sentence", "said again", "shipping"])
    def should_not_match_english_short_words_when_not_intended(self, text):
        """英文短词带尾空格，避免 hi 命中 this、ai 命中 again 这类误匹配。"""
        assert make_responder().matched_rule(text) == "fallback"

    def should_match_greeting_when_text_has_uppercase_hello(self):
        assert make_responder().matched_rule("HELLO 呀") == "greet"

    def should_fallback_when_text_blank(self):
        responder = make_responder()
        assert responder.matched_rule("") == "fallback"
        assert responder.reply("")  # 空输入也不能崩

    def should_expose_last_intent_when_replied(self):
        """上层靠它分清“这条是哪段路由答的”，必须与 matched_rule 同口径（v1.6 C8）。"""
        responder = make_responder()

        responder.reply("你好呀")
        assert responder.last_intent == "greet"

        responder.reply("zzz qqq 火星文")
        assert responder.last_intent == "fallback"

    def should_clear_last_intent_when_reset(self):
        responder = make_responder()
        responder.reply("你好呀")

        responder.reset()

        assert responder.last_intent == ""


class TestStateAwareness:
    def should_report_all_vitals_when_asked_feeling(self):
        responder = make_responder(hunger=80.0, mood=80.0, energy=80.0)

        reply = responder.reply("你现在感觉怎么样")

        assert "开心得尾巴都在打转" in reply
        assert "肚子饱饱的" in reply
        assert "精力满满" in reply

    def should_report_low_state_when_vitals_dangerous(self):
        responder = make_responder(hunger=5.0, mood=10.0, energy=5.0)

        reply = responder.reply("你现在感觉怎么样")

        assert "心情低落得很" in reply
        assert "饿到没力气说话" in reply
        assert "困成一团" in reply

    def should_mention_mid_band_when_hunger_middle(self):
        responder = make_responder(hunger=45.0)

        assert "有点小饿了" in responder.reply("你饿不饿")

    def should_invite_sleep_when_night_and_asked_about_tired(self):
        responder = make_responder(hour=23, energy=30.0)

        reply = responder.reply("好困呀")

        assert "夜深啦" in reply
        assert "眼皮已经开始打架" in reply

    def should_report_period_when_asked_time(self):
        responder = make_responder(hour=15)

        reply = responder.reply("现在几点了")

        assert "下午" in reply and "15 点" in reply

    def should_teach_settings_when_asked_ai_config(self):
        reply = make_responder().reply("为什么连不上 AI")

        assert "设置" in reply and "API Key" in reply


class TestReplyBehaviour:
    def should_not_explain_local_mode_on_first_reply(self):
        """v1.4 C6：首句就告诉用户「这是本地模式」最像客服机器人，说明改由功能问答承担。"""
        responder = make_responder()

        assert LOCAL_MODE_HINT not in responder.reply("你好")
        assert LOCAL_MODE_HINT in responder.reply("你能做什么")

    def should_rotate_pool_when_repeated_same_intent(self):
        responder = make_responder()

        first = responder.reply("随便说点什么奇怪的")
        second = responder.reply("随便说点什么奇怪的")

        assert first != second  # 连续两次不重复，避免机械感

    def should_be_deterministic_when_same_sequence(self):
        seq = ["你好", "讲个笑话", "你是谁"]

        a = [make_responder().reply(t) for t in seq]
        b = [make_responder().reply(t) for t in seq]

        assert a == b  # 不用 random：测试可断言、行为可复现

    def should_not_revive_first_reply_hint_when_reset(self):
        """取向改了就要锁住：reset 不得把「首句挂能力说明」这个旧行为放回来。"""
        responder = make_responder()
        responder.reply("你好")

        responder.reset()

        assert LOCAL_MODE_HINT not in responder.reply("你好")

    def should_read_context_each_call_when_vitals_change(self):
        state = {"mood": 90.0}
        responder = LocalResponder(lambda: LocalContext(mood=state["mood"]))

        high = responder.reply("今天心情怎么样")
        state["mood"] = 10.0
        low = responder.reply("今天心情怎么样")

        assert "开心得尾巴都在打转" in high
        assert "心情低落得很" in low


class TestSkinAwareness:
    """本地小脑的自称必须跟着皮肤走，不能换了小猴还自称猫。"""

    def should_call_itself_small_cat_when_skin_default(self):
        reply = make_responder().reply("你是谁")
        assert SPECIES_LABEL["cat"] in reply

    @pytest.mark.parametrize(("skin", "label", "sound"), [
        ("dog", SPECIES_LABEL["dog"], SPECIES_SOUND["dog"]),
        ("monkey", SPECIES_LABEL["monkey"], SPECIES_SOUND["monkey"]),
    ])
    def should_switch_self_word_when_skin_changed(self, skin, label, sound):
        responder = make_responder(skin=skin)

        assert label in responder.reply("你是谁")
        assert sound in responder.reply("zzz qqq")

    def should_fall_back_to_cat_when_skin_unknown(self):
        assert make_responder(skin="dragon").reply("你是谁").count(SPECIES_LABEL["cat"]) >= 1


class TestBrainRouting:
    """四段路由的后三段：同义泛化（NB）/ 知识检索（BM25+精配）/ 报名字。"""

    @pytest.mark.parametrize(("text", "rule"), [
        ("我心态有点崩", "stress"),                 # 关键词表里没有「心态有点崩」
        ("今天被组长说了一顿", "stress"),
        ("周一要交的东西还没动", "work"),
    ])
    def should_route_paraphrase_when_keyword_missed(self, text, rule):
        pytest.importorskip("jieba", reason="同义泛化依赖真分词粒度")
        assert make_responder().matched_rule(text) == rule

    @pytest.mark.parametrize(("text", "rule"), [
        ("番茄工作法是什么", "knowledge"),
        ("一直坐着腰疼", "knowledge"),
        ("给我打打气", "knowledge"),
    ])
    def should_answer_from_corpus_when_intent_not_matched(self, text, rule):
        assert make_responder().matched_rule(text) == rule

    @pytest.mark.parametrize("text", ["zzz qqq 火星文", "qwertyuiop", "啊哦呢"])
    def should_admit_limit_when_nothing_matched(self, text):
        assert make_responder().matched_rule(text) == "fallback"

    def should_acknowledge_and_reuse_name_when_user_introduces(self):
        responder = make_responder()

        first = responder.reply("我叫林克")
        second = responder.reply("你好")

        assert "林克" in first
        assert "林克" in second          # 记住了，下次打招呼会带上

    def should_not_write_memory_to_any_store_when_told_name(self):
        """隐私红线：本地小脑的记忆只在会话内，reset 就清干净。"""
        responder = make_responder()
        responder.reply("我叫林克")

        responder.reset()

        assert responder._engine.memory.user_name == ""
        assert "林克" not in responder.reply("你好")


class TestFollowUp:
    def should_pick_up_last_topic_when_reply_is_just_ack(self):
        responder = make_responder()
        responder.reply("我在写代码")

        rule = responder.matched_rule("然后呢")
        reply = responder.reply("然后呢")

        assert rule == "follow_up"
        assert "代码" in reply            # 把球踢回去，不冷场

    def should_not_follow_up_when_no_topic_recorded(self):
        responder = make_responder()

        assert responder.matched_rule("嗯") == "fallback"

    def should_not_steal_previous_topic_when_answering_new_advice(self):
        """v1.4 C6：拿上一轮话题套到新句子上会说出「「消息」你能把选项说具体一点吗」。

        旧断言（advice 必须引用上一轮的「工作」）已被否定：只引用本轮实词，
        本轮抽不到就说「这件事」。跨轮承接交给 follow_up（回指）那条链路。
        """
        responder = make_responder()
        responder.reply("我想问问换工作的事")

        reply = responder.reply("我该怎么选")

        assert "这件事" in reply
        assert "工作" not in reply


class TestFallbackEcho:
    """兜底回显必须说这一轮听到的东西，不能拿上一轮的话题冒充。"""

    def should_not_record_topic_when_turn_is_small_talk(self):
        responder = make_responder()

        responder.reply("你好")

        assert responder._engine.memory.last_topic == ""

    def should_not_echo_garbage_but_offer_real_asks(self):
        """真机反馈：「你好」后发一句无主题词，既不回显上一轮，也不复读乱码。"""
        responder = make_responder()
        responder.reply("你好")

        reply = responder.reply("asdfgh")

        assert "我听到的是「你好」" not in reply
        assert "asdfgh" not in reply
        assert "试试问" in reply                # 承认听不懂，但要给出路

    def should_reference_last_topic_when_current_turn_is_anaphoric(self):
        """回指时引用上一轮话题仍然成立（不能把这条改坏）。"""
        responder = make_responder()
        responder.reply("我在写代码")

        assert "代码" in responder.reply("嗯")

    def should_rotate_every_example_ask_when_garbage_repeated(self):
        """连续兜底要把例子池轮完（含尾部回绕），否则用户每次都看到同三句。"""
        responder = make_responder()

        seen = [responder.reply("zzz qqq") for _ in range(len(corpus.EXAMPLE_ASKS) + 2)]

        for ask in corpus.EXAMPLE_ASKS:
            assert any(ask in reply for reply in seen)


class TestQuestionPriority:
    """求知型问句不得被「报宠物自己状态」的规则接住（真机反馈的「答非所问」）。"""

    @pytest.mark.parametrize(("text", "rule"), [
        ("猫为什么喜欢睡觉", "knowledge"),      # 以前被 SLEEP_WORDS 的「睡」抢走，答成自己的精力
        ("睡不着怎么办", "knowledge"),
        ("狗为什么摇尾巴", "knowledge"),
    ])
    def should_prefer_knowledge_when_question_hits_corpus(self, text, rule):
        assert make_responder().matched_rule(text) == rule

    @pytest.mark.parametrize(("text", "rule"), [
        ("我心态崩了怎么办", "stress"),          # 「怎么办」是求对策不是求知，不得被知识层抢走
        ("你去睡吧", "sleep"),
        ("现在几点了", "time"),                    # 问时间不是一句知识型提问
    ])
    def should_keep_rule_when_question_is_about_user_or_pet(self, text, rule):
        assert make_responder().matched_rule(text) == rule

    def should_not_answer_question_when_knowledge_hit_is_weak(self):
        """只有精配或压倒性得分才能抢在规则前：模棱两可的检索分不得翻盘。"""
        assert make_responder().matched_rule("你心情好吗") == "feel"


class TestUserFirstCopy:
    """文案取向（v1.4 C6）：用户内容型回复的第一句必须接住用户，不播报宠物状态。"""

    @pytest.mark.parametrize("text", ["我心态有点崩", "我心情不好", "今天被组长说了一顿"])
    def should_not_broadcast_pet_state_when_user_confides(self, text):
        responder = make_responder(mood=90.0, hunger=90.0, energy=90.0)

        for _ in range(4):                      # 轮换池全走一遍，四条都不能带状态播报
            reply = responder.reply(text)
            assert "尾巴都在打转" not in reply
            assert "肚子饱饱的" not in reply
            assert "精力满满" not in reply

    def should_not_open_greeting_with_vitals_report(self):
        responder = make_responder(mood=90.0, hunger=90.0, energy=90.0)

        for _ in range(4):
            assert "开心得尾巴都在打转" not in responder.reply("你好")

    def should_quote_user_word_when_confiding(self):
        """接住的最小证据：回复里要出现用户自己句中的实词（抽哪一个由 idf 决定）。"""
        responder = make_responder()

        replies = [responder.reply("被领导批评了，心里堵得慌") for _ in range(4)]

        assert any(word in r for r in replies for word in ("领导", "批评", "堵得慌"))

    def should_keep_reporting_state_when_user_asks_about_pet(self):
        """反面对照：用户主动问宠物时，状态播报仍然是正确答案。"""
        responder = make_responder(mood=90.0, hunger=90.0, energy=90.0)

        assert "尾巴都在打转" in responder.reply("你现在感觉怎么样")


class TestVitalBands:
    """状态话术的四档分档：只测两头会让中间档的文案烂在仓库里没人知道写错了。"""

    @pytest.mark.parametrize(("vitals", "expected"), [
        ({"mood": 50.0}, "心情平平，有你陪着就不错"),      # MOOD_MID ≤ mood < MOOD_HIGH
        ({"mood": 30.0}, "有点提不起劲"),                  # MOOD_LOW ≤ mood < MOOD_MID
        ({"hunger": 30.0}, "饿得前爪都发软"),              # HUNGER_LOW ≤ hunger < HUNGER_MID
        ({"energy": 50.0}, "略有点困"),                    # ENERGY_MID ≤ energy < ENERGY_HIGH
    ])
    def should_report_middle_band_when_vital_is_between_thresholds(self, vitals, expected):
        values = {"mood": 90.0, "hunger": 90.0, "energy": 90.0}   # 其余项顶到最高档，隔离变量
        values.update(vitals)
        responder = make_responder(**values)

        assert expected in responder.reply("你现在感觉怎么样")

    def should_add_comfort_when_knowledge_answered_in_low_mood(self):
        """低落时知识也得带点温度，不能像百科词条。"""
        responder = make_responder(mood=10.0)
        ctx = LocalContext(mood=10.0)

        reply = responder._answer_knowledge(("猫一天要睡 12 到 16 小时",), ctx)

        assert reply.endswith("（今天不容易，说完这条你先歇口气）")

    def should_not_add_comfort_when_answer_already_soft_or_mood_ok(self):
        """已有省略号（本身就是低语气）或心情不差时，不叠后缀以免两段情绪撞在一起。"""
        responder = make_responder()
        already_soft = responder._answer_knowledge(("我陪你…",), LocalContext(mood=10.0))
        bright = responder._answer_knowledge(("猫一天要睡 12 到 16 小时",), LocalContext(mood=80.0))

        assert "今天不容易" not in already_soft
        assert "今天不容易" not in bright
