"""nlp 原语：分词后端惰性选择、bigram 回退、n-gram 与停用词（v1.3 本地小脑）。"""
import sys

import pytest

from WisePet.app import nlp
from WisePet.app.nlp import STOPWORDS, Tokenizer, bigram_split, normalize, overlap_score


class TestBackendSelection:
    def should_stay_unresolved_when_not_used_yet(self):
        """关键回归点：构造分词器不能加载词典，否则冷启动从 0.4s 变 1s。"""
        assert Tokenizer().backend == "pending"

    def should_use_injected_splitter_when_given(self):
        tokenizer = Tokenizer(splitter=lambda text: ["心情", "不好"])
        assert tokenizer.tokenize("我心情不好") == ["心情", "不好"]
        assert tokenizer.backend == "custom"

    def should_fall_back_to_bigram_when_splitter_raises(self):
        def boom(_text):
            raise RuntimeError("分词器炸了")

        tokenizer = Tokenizer(splitter=boom)
        assert tokenizer.tokenize("心情不好") == bigram_split("心情不好")

    def should_degrade_to_bigram_when_third_party_missing(self, monkeypatch):
        monkeypatch.setattr(nlp, "_load_jieba", lambda: None)
        tokenizer = Tokenizer()
        tokenizer.tokenize("心情不好")
        assert tokenizer.backend == "bigram"

    def should_report_and_use_real_dictionary_when_present(self):
        pytest.importorskip("jieba", reason="真分词后端需要 jieba")
        tokenizer = Tokenizer()
        tokens = tokenizer.tokenize("我心情不太好")
        assert tokenizer.backend == "jieba"
        assert "心情" in tokens

    def should_survive_when_third_party_init_raises(self, monkeypatch):
        """词典加载抛异常不能冒到聊天链路：默默换成内置分词继续回答。"""
        def explode():
            raise RuntimeError("词典坏了")

        monkeypatch.setattr(nlp, "_load_jieba", explode)
        tokenizer = Tokenizer()
        assert tokenizer.tokenize("心情不好") == bigram_split("心情不好")
        assert tokenizer.backend == "bigram"

    def should_degrade_when_import_itself_fails(self, monkeypatch):
        """真·没装 jieba 的那条路（AC-BRAIN-6）：不能靠 monkeypatch _load_jieba 代替。"""
        import builtins

        real_import = builtins.__import__

        def blocked(name, *args, **kwargs):
            if name == "jieba":
                raise ImportError("no jieba here")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", blocked)
        tokenizer = Tokenizer()
        assert tokenizer.tokenize("心情不好") == bigram_split("心情不好")
        assert tokenizer.backend == "bigram"

    def should_still_tokenize_when_dictionary_tuning_fails(self, monkeypatch):
        """并入领域词失败只该丢掉调优，分词本身照常用真后端。"""
        class PartialJieba:
            __version__ = "test"

            @staticmethod
            def setLogLevel(_level):
                return None

            @staticmethod
            def add_word(_word):
                raise RuntimeError("词典只读")

            @staticmethod
            def cut(text):
                return list(text)

        monkeypatch.setitem(sys.modules, "jieba", PartialJieba())
        tokenizer = Tokenizer()
        assert tokenizer.tokenize("心情") == ["心", "情"]
        assert tokenizer.backend == "jieba"


class TestBigramSplit:
    def should_split_cjk_run_into_adjacent_pairs_when_builtin(self):
        assert bigram_split("心情不好") == ["心情", "情不", "不好"]

    def should_keep_latin_and_digits_whole_when_builtin(self):
        assert bigram_split("api2 key") == ["api2", "key"]

    def should_return_empty_when_no_word_chars(self):
        assert bigram_split("，。！ ") == []


class TestTokenAndGrams:
    def should_drop_tokens_without_word_chars(self):
        tokenizer = Tokenizer(splitter=lambda _t: ["心情", "，", "  ", ""])
        assert tokenizer.tokenize("心情，") == ["心情"]

    def should_keep_content_words_in_grams_when_builtin(self):
        tokenizer = Tokenizer(splitter=lambda _t: ["心情", "不好"])
        grams = tokenizer.grams("心情不好")
        assert "心情" in grams and "不好" in grams and "心情不好" in grams

    def should_not_bridge_bigram_over_stopword(self):
        """停用词既不单独成词，也不参与拼接（否则「今天被」这类碎块会污染检索）。"""
        tokenizer = Tokenizer(splitter=lambda _t: ["心情", "的", "不好"])
        assert tokenizer.grams("心情的不好") == ["心情", "不好"]

    def should_be_lowercase_insensitive_when_normalizing(self):
        assert normalize("  HELLO 猫 ") == "hello 猫"
        assert Tokenizer(splitter=lambda t: [t]).grams("HELLO") == ["hello"]


class TestSimilarity:
    def should_return_zero_when_either_side_empty(self):
        assert overlap_score([], ["心情"]) == 0.0
        assert overlap_score(["心情"], []) == 0.0

    def should_return_one_when_sides_identical(self):
        assert overlap_score(["心情", "不好"], ["不好", "心情"]) == 1.0

    def should_list_stopwords_as_expected(self):
        assert "的" in STOPWORDS and "有点" in STOPWORDS and "the" in STOPWORDS
