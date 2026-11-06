"""DialogEngine：统计层理解（意图分类 / BM25 检索 / 话题跟踪 / 会话内记忆）。

大部分用例注入 bigram 分词器（不依赖 jieba，任何环境结果一致）；
需要真分词才能成立的「同义泛化」用例单独 importorskip 标注。
"""
import pytest

from WisePet.app.corpus import ACTIVITY_PATTERNS, KNOWLEDGE, NAME_PATTERNS, Knowledge
from WisePet.app.dialog_engine import (
    Bm25Index,
    DialogEngine,
    IntentClassifier,
    UserMemory,
)
from WisePet.app.nlp import Tokenizer, bigram_split


def brain():
    """注入零依赖分词器的引擎（检索/短语/记忆层与真分词结果一致）。"""
    return DialogEngine(tokenizer=Tokenizer(splitter=bigram_split))


def controlled_engine():
    """自带小语料 + 空格分词：用于需要精确控制 df 的话题/打分断言。

    语料故意让「猫咪」出现在两条问法里（df=2），「狗狗」只出现一次（df=1）。
    """
    entries = (Knowledge("猫事", ("猫咪 喝水", "猫咪 吃饭", "狗狗 睡觉"),
                         ("喵答1", "喵答2")),)
    samples = {"a": ("猫咪 饿了",)}
    return DialogEngine(tokenizer=Tokenizer(splitter=lambda text: text.split()),
                        knowledge=entries, samples=samples)


def clf_token(text: str) -> list[str]:
    """单字 + 相邻拼块，与 IntentClassifier 训练时的 grams 保持一致。"""
    return Tokenizer(splitter=lambda t: list(t)).grams(text)


class TestLazyBuild:
    def should_not_build_index_until_first_question(self):
        engine = DialogEngine()
        assert engine._index is None
        assert engine.backend == "pending"

        engine.lookup("番茄工作法是什么")
        assert engine._index is not None


class TestKnowledgeRetrieval:
    def should_answer_when_ask_written_verbatim(self):
        found = brain().lookup("番茄工作法是什么")
        assert found is not None
        assert found.entry.topic == "番茄钟"

    def should_answer_when_query_is_longer_than_ask(self):
        """用户把语料问句包进长句里也算命中：小语料下字面精配比打分可靠。"""
        found = brain().lookup("你好，我想问下久坐有什么危害呀")
        assert found is not None and found.entry.topic == "久坐"

    def should_reject_when_only_generic_word_overlaps(self):
        assert brain().lookup("今天有点不一样") is None

    def should_reject_when_nothing_in_corpus_matches(self):
        assert brain().lookup("zzz qqq 火星文") is None

    def should_return_none_when_corpus_is_empty(self):
        index = Bm25Index(Tokenizer(splitter=bigram_split), entries=())
        assert index.doc_count == 0
        assert index.search(["番茄", "工作法"], "番茄工作法") is None

    def should_skip_entry_without_any_asks(self):
        """空问法条目不进索引（防御语料写坏，不能让检索崩）。"""
        index = Bm25Index(Tokenizer(splitter=bigram_split),
                          entries=(Knowledge("空", (), ("没有问法",)),) + KNOWLEDGE)
        assert index.doc_count > 0

    def should_skip_ask_that_tokenizes_to_nothing(self):
        """问法全为标点时分词为空，必须跳过而不是当成长度 0 的文档污染均值。"""
        plain = Bm25Index(Tokenizer(splitter=bigram_split), entries=KNOWLEDGE)
        polluted = Bm25Index(Tokenizer(splitter=bigram_split),
                             entries=(Knowledge("坏条目", ("，。！"), ("答",)),) + KNOWLEDGE)
        assert polluted.doc_count == plain.doc_count

    def should_skip_phrase_hit_when_text_has_no_content(self):
        """归一后为空串时直接不命中：空串是任意问法的子串，不拦就会命中语料第一条。"""
        index = Bm25Index(Tokenizer(splitter=bigram_split), entries=KNOWLEDGE)
        assert index._phrase_hit("") is None
        assert index._phrase_hit("番茄工作法是什么") is not None


class TestIntentClassifier:
    def should_reject_when_evidence_too_thin(self):
        engine = brain()
        engine._ensure_ready()
        assert engine.analyze("林克") is None

    def should_produce_empty_when_no_in_vocab_gram(self):
        engine = brain()
        engine._ensure_ready()
        assert engine._classifier.predict(["根本不存在词元"]) == []

    def should_reject_when_two_classes_tie(self):
        """两个类语料一模一样 → 后验打平，差值门槛必须把它挡掉。"""
        clf = IntentClassifier(Tokenizer(splitter=lambda t: list(t)),
                               samples={"a": ("甲乙丙",), "b": ("甲乙丙",)})
        hits = clf.predict(clf_token("甲乙丙"))
        assert clf.accept(hits) is None

    def should_accept_clear_winner_when_grams_only_in_one_class(self):
        clf = IntentClassifier(Tokenizer(splitter=lambda t: list(t)),
                               samples={"a": ("甲乙丙",), "b": ("丁戊己",), "c": ("庚辛壬",)})
        hit = clf.accept(clf.predict(clf_token("甲乙丙")))
        assert hit is not None and hit.intent == "a"
        assert hit.source == "naive-bayes"

    def should_classify_paraphrase_when_real_dictionary_available(self):
        pytest.importorskip("jieba", reason="同义泛化依赖真分词粒度")
        engine = DialogEngine()
        assert engine.analyze("我心态有点崩").intent == "stress"
        assert engine.analyze("周一要交的东西还没动").intent == "work"


class TestTopicAndAnaphora:
    def should_prefer_rarest_corpus_word_as_topic(self):
        """语料里越稀有的实词越具体，拿它当话题才不会反问到空处。"""
        assert controlled_engine().topic_of("猫咪 狗狗 鲨鱼") == "狗狗"

    def should_take_longest_token_when_none_in_corpus(self):
        assert controlled_engine().topic_of("鲨鱼 老鹰蛋") == "老鹰蛋"

    def should_return_empty_topic_when_only_stopwords(self):
        assert controlled_engine().topic_of("我的 有点 就是") == ""
        assert brain().topic_of("") == ""

    @pytest.mark.parametrize("text", ["嗯", "哦", "然后", "然后呢", "为什么", "继续"])
    def should_recognize_anaphoric_short_replies(self, text):
        assert brain().is_anaphoric(text) is True

    @pytest.mark.parametrize("text", ["我在写代码", "今天心情不好", "喝水"])
    def should_not_treat_content_input_as_anaphoric(self, text):
        assert brain().is_anaphoric(text) is False


class TestMemoryExtraction:
    @pytest.mark.parametrize(("text", "expected"), [
        ("我叫林克", "林克"),
        ("你可以叫我小球", "小球"),
        ("我在写代码", "写代码"),
        ("我正在准备考试", "准备考试"),
    ])
    def should_extract_object_when_pattern_hit(self, text, expected):
        engine = brain()
        name = engine.extract_memory(text, NAME_PATTERNS)
        activity = engine.extract_memory(text, ACTIVITY_PATTERNS)
        assert (name or activity) == expected

    def should_return_empty_when_no_pattern(self):
        engine = brain()
        assert engine.extract_memory("今天天气不错", NAME_PATTERNS) == ""
        assert engine.extract_memory("今天天气不错", ACTIVITY_PATTERNS) == ""

    def should_cut_long_object_when_value_too_long(self):
        value = brain().extract_memory("我叫" + "阿巴阿巴" * 6, NAME_PATTERNS)
        assert len(value) <= 12

    def should_stop_at_punctuation_when_clause_follows(self):
        assert brain().extract_memory("我叫林克，今天很累", NAME_PATTERNS) == "林克"


class TestUserMemory:
    def should_track_topic_and_intent_when_noted(self):
        mem = UserMemory()
        mem.note("stress", "腰疼")
        assert mem.last_intent == "stress" and mem.last_topic == "腰疼"
        assert mem.turns == 1

    def should_keep_previous_topic_when_note_is_blank(self):
        mem = UserMemory()
        mem.note("work", "代码")
        mem.note("", "")
        assert mem.last_topic == "代码" and mem.last_intent == "work"

    def should_count_seen_intents(self):
        mem = UserMemory()
        mem.note("greet", "哈")
        mem.note("greet", "哈")
        assert mem.seen_intents["greet"] == 2

    def should_reset_memory_when_engine_reset(self):
        engine = brain()
        engine.memory.remember(user_name="林克", activity="写代码")
        engine.reset()
        assert engine.memory.user_name == ""
        assert engine.memory.turns == 0
