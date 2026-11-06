"""语料完整性：本地小脑的能力上限就是这份语料，所以「语料本身」要有测试守着。

为什么单独立一个文件：路由与文案的测试只能发现「这条句子没接住」，
发现不了「有人删了十条知识」「某类意图只剩两句样本」这种整体退化。
"""
import pytest

from WisePet.app import corpus
from WisePet.app.dialog_engine import DialogEngine
from WisePet.app.local_responder import LocalContext, LocalResponder

#: 自我报告类意图：它们的回复是「报宠物自己的情况」，训练样本里混进求知问句，
#: 就会把「猫为什么爱睡觉」这类问题训成答非所问（真机反馈的病灶之一）。
#: help/ai/time 不在列：它们的回复是给说明与事实，求知句式放进去是正确的。
STATE_INTENTS = frozenset({
    "greet", "who", "feel", "food", "sleep", "praise", "insult", "love",
    "joke", "weather", "thanks", "bye",
})


class TestCorpusScale:
    @pytest.mark.parametrize("intent", sorted(corpus.INTENT_SAMPLES))
    def should_have_enough_samples_per_intent(self, intent):
        """每类不少于 8 句：少于这个量，朴素贝叶斯的词元覆盖太窄，用户换个说法就落空。"""
        assert len(corpus.INTENT_SAMPLES[intent]) >= 8

    def should_keep_knowledge_at_current_scale(self):
        """知识条目是「能答什么」的清单，跌破下限就是能力退化。"""
        assert len(corpus.KNOWLEDGE) >= 40

    def should_have_unique_topics_in_knowledge(self):
        topics = [entry.topic for entry in corpus.KNOWLEDGE]
        assert len(topics) == len(set(topics))

    @pytest.mark.parametrize("entry", corpus.KNOWLEDGE, ids=lambda e: e.topic)
    def should_have_retrievable_variants_and_swappable_answers(self, entry):
        """asks 少于 3 条召回太窄；answers 少于 2 条就轮换不起来，连续问会复读。"""
        assert len(entry.asks) >= 3
        assert len(entry.answers) >= 2
        assert len(set(entry.answers)) == len(entry.answers)

    @pytest.mark.parametrize("entry", corpus.KNOWLEDGE, ids=lambda e: e.topic)
    def should_retrieve_itself_from_every_ask_variant(self, entry):
        """每条问法单独检索都要回到自己：答案池必须能回答它自己的任何一个问法。

        真机踩过：把「眨眼/纸盒/猫睡觉」塞进同一条、共用一个答案池，
        问猫为什么爱睡觉就答出了纸盒。那条知识随后被拆成两条。
        """
        engine = DialogEngine()
        for ask in entry.asks:
            found = engine.lookup(ask)
            assert found is not None, ask
            assert found.entry.topic == entry.topic, ask


class TestCorpusIntegrity:
    def should_cover_every_rule_name_with_samples(self):
        """意图样本的键必须与规则名一一对应，否则分类器会产出路由表里没有的意图。"""
        rules = {name for name, _, _ in LocalResponder(lambda: LocalContext())._rules}
        assert set(corpus.INTENT_SAMPLES) == rules

    @pytest.mark.parametrize("intent", sorted(STATE_INTENTS))
    def should_not_train_knowledge_question_into_state_intent(self, intent):
        """求知问句不得当成状态类意图的训练样本：那会把「该查知识」教成「该报状态」。"""
        for sample in corpus.INTENT_SAMPLES[intent]:
            assert "为什么" not in sample

    def should_have_examples_for_fallback_guide(self):
        assert len(corpus.EXAMPLE_ASKS) >= corpus.EXAMPLE_COUNT
        assert all(ask for ask in corpus.EXAMPLE_ASKS)

    def should_have_anaphoric_words_used_by_follow_up(self):
        assert "然后" in corpus.ANAPHORIC_WORDS
