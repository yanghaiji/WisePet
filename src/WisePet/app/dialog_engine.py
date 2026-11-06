"""DialogEngine：本地小脑的统计语言层（朴素贝叶斯意图 + BM25 检索 + 话题跟踪）。

放在 app 层的原因：它是「编排策略」，不是外部服务，也不碰 Qt/网络。
零训练成本：意图样例与知识条目在首次 analyze 时才建索引（纯 CPU 微秒级），
所以冷启动完全不受影响；jieba 词典加载也只发生在这一次。

确定性：无 random、无时间依赖，同输入同结果，测试可直接断言。
"""
from __future__ import annotations

import logging
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .corpus import ANAPHORIC_WORDS, INTENT_SAMPLES, KNOWLEDGE, QUESTION_MARKS, Knowledge
from .nlp import STOPWORDS, Tokenizer, has_cjk, normalize

logger = logging.getLogger(__name__)

#: BM25 参数（学界的常规取值，小语料下 1.5/0.75 比极端值稳）
BM25_K1 = 1.5
BM25_B = 0.75

#: 意图接受门槛。confidence 是 softmax 后的真后验，20 个类均匀分布才 1/N=0.05，
#: 所以 0.25 的含意是「比均匀分布高 5 倍」，不是「有 25% 把握」；
#: 另加 top2 差值门槛，把「两个类势均力敌」的歧义输入挡在外面。
INTENT_MIN_CONF = 0.25
INTENT_MIN_MARGIN = 0.18

#: 至少要撞上这么多个训练过的词元，不然一句口语里一个碰巧字就能“猜中”意图
INTENT_MIN_MATCH = 2

#: 知识检索：默认要求至少命中这么多个不重复词元；
#: 只命中 1 个时得分必须高过 KNOWLEDGE_STRONG_SCORE（实测单一虚词约 3~5，
#: 真正的主题词命中约 10+）才放行，宁可不硬答
KNOWLEDGE_MIN_SCORE = 2.0
KNOWLEDGE_MIN_TERMS = 2
KNOWLEDGE_STRONG_SCORE = 6.0

#: 整句问法直接出现在用户话里时的标记分（优先于词袋得分）
PHRASE_SCORE = 99.0

#: 用户话比语料问法短时，至少要有这么长才允许反向包含
PHRASE_MIN_QUERY_LEN = 4

#: 话题词最短长度（单字太泛，做不了追问引用）
TOPIC_MIN_LEN = 2


@dataclass(frozen=True)
class IntentHit:
    """一次意图判定。source 区分命中来源，供日志与测试诊断。"""

    intent: str
    confidence: float
    source: str        # naive-bayes / anaphoric / none
    evidence: int = 0  # 命中的训练词元个数


@dataclass(frozen=True)
class KnowledgeHit:
    entry: Knowledge
    score: float


@dataclass
class UserMemory:
    """会话内记忆（不写盘、不上传，退出即清——这是本地小脑的隐私边界）。"""

    user_name: str = ""
    activity: str = ""
    last_topic: str = ""
    last_intent: str = ""
    turns: int = 0
    seen_intents: Counter = field(default_factory=Counter)

    def note(self, intent: str, topic: str) -> None:
        self.turns += 1
        if intent:
            self.last_intent = intent
            self.seen_intents[intent] += 1
        if topic:
            self.last_topic = topic

    def remember(self, user_name: str = "", activity: str = "") -> None:
        if user_name:
            self.user_name = user_name
        if activity:
            self.activity = activity


class Bm25Index:
    """把每条知识问法当一个文档；检索时返回得分最高的知识条目。"""

    def __init__(self, tokenizer: Tokenizer, entries: tuple[Knowledge, ...] = KNOWLEDGE):
        self._docs: list[tuple[int, Counter, int]] = []   # (entry_idx, 词频, 长度)
        df: Counter = Counter()
        for idx, entry in enumerate(entries):
            for ask in entry.asks:
                grams = tokenizer.grams(ask)
                if not grams:
                    continue
                freq = Counter(grams)
                self._docs.append((idx, freq, len(grams)))
                df.update(freq.keys())
        self._entries = entries
        self._df = df
        self._n = len(self._docs)
        self._avgdl = (sum(d[2] for d in self._docs) / self._n) if self._n else 0.0

    def _idf(self, term: str) -> float:
        df = self._df.get(term, 0)
        return math.log(1.0 + (self._n - df + 0.5) / (df + 0.5))

    def search(self, grams: list[str], text: str = "") -> KnowledgeHit | None:
        if not self._n or not grams:
            return None
        phrase = self._phrase_hit(text)
        if phrase is not None:
            return phrase
        best_idx, best_score = -1, 0.0
        query = set(grams)
        for idx, freq, dl in self._docs:
            score = 0.0
            matched = 0
            for term in query:
                f = freq.get(term, 0)
                if not f:
                    continue
                matched += 1
                denom = f + BM25_K1 * (1.0 - BM25_B + BM25_B * dl / self._avgdl)
                score += self._idf(term) * f * (BM25_K1 + 1.0) / denom
            # 只靠一个虚词搭上的高 idf 不算数：要两个词元，或总分足够压倒性
            if matched < KNOWLEDGE_MIN_TERMS and score < KNOWLEDGE_STRONG_SCORE:
                continue
            if score > best_score:
                best_idx, best_score = idx, score
        if best_idx < 0 or best_score < KNOWLEDGE_MIN_SCORE:
            return None
        return KnowledgeHit(entry=self._entries[best_idx], score=round(best_score, 3))

    def doc_freq(self, term: str) -> int:
        return self._df.get(term, 0)

    def _phrase_hit(self, text: str) -> KnowledgeHit | None:
        """字面精配：语料里的问法原样出现在用户话里（或反之）就直接用。

        小语料下这一层比词袋更可靠：用户就把问句贴过来了，没理由靠打分去猜。
        """
        norm = normalize(text)
        if not norm:
            return None
        for entry in self._entries:
            for ask in entry.asks:
                if ask in norm or (len(norm) >= PHRASE_MIN_QUERY_LEN and norm in ask):
                    return KnowledgeHit(entry=entry, score=PHRASE_SCORE)
        return None

    @property
    def doc_count(self) -> int:
        return self._n


class IntentClassifier:
    """多项式朴素贝叶斯：由 INTENT_SAMPLES 现场训练，无外部模型文件。

    得分是未归一的 log 后验（log 先验 + Σ log 条件概率），直接做 softmax 就是真后验；
    不能先除句长——那相当于给 softmax 加了温度，会把所有类别压平成 1/N，
    绝对值门槛就永远过不了（实测过这个坑）。
    """

    def __init__(self, tokenizer: Tokenizer,
                 samples: dict[str, tuple[str, ...]] = INTENT_SAMPLES):
        class_counts: dict[str, Counter] = defaultdict(Counter)
        doc_counts: Counter = Counter()
        for intent, texts in samples.items():
            for text in texts:
                grams = tokenizer.grams(text)
                class_counts[intent].update(grams)
                doc_counts[intent] += 1
        self._class_counts = dict(class_counts)
        self._class_totals = {k: sum(c.values()) for k, c in class_counts.items()}
        self._vocab_size = len({g for c in class_counts.values() for g in c})
        self._docs_total = sum(doc_counts.values()) or 1
        self._doc_counts = doc_counts

    def predict(self, grams: list[str]) -> list[IntentHit]:
        in_vocab = [g for g in grams if self._in_vocab(g)]
        if len(in_vocab) < INTENT_MIN_MATCH:
            return []
        scored: list[tuple[str, float]] = []
        for intent, counts in self._class_counts.items():
            total = self._class_totals[intent]
            log_prob = math.log(self._doc_counts[intent] / self._docs_total)
            for gram in in_vocab:
                # Laplace 平滑：(计数+1) / (总计数 + 词表规模)
                log_prob += math.log((counts.get(gram, 0) + 1.0) / (total + self._vocab_size))
            scored.append((intent, log_prob))
        scored.sort(key=lambda kv: (-kv[1], kv[0]))
        top = scored[0][1]
        exps = [(intent, math.exp(score - top)) for intent, score in scored]
        denom = sum(value for _, value in exps) or 1.0
        return [IntentHit(intent, round(value / denom, 3), "naive-bayes", len(in_vocab))
                for intent, value in exps[:3]]

    def accept(self, hits: list[IntentHit]) -> IntentHit | None:
        """后验与领先差值双门槛；都不过就返回 None（宁可不答）。"""
        if not hits:
            return None
        best = hits[0]
        margin = best.confidence - (hits[1].confidence if len(hits) > 1 else 0.0)
        if best.confidence >= INTENT_MIN_CONF and margin >= INTENT_MIN_MARGIN:
            return best
        return None

    def _in_vocab(self, gram: str) -> bool:
        return any(gram in counts for counts in self._class_counts.values())


class DialogEngine:
    """把分词、意图、检索、话题、记忆串成一次「理解」。"""

    def __init__(self, tokenizer: Tokenizer | None = None,
                 knowledge: tuple[Knowledge, ...] = KNOWLEDGE,
                 samples: dict[str, tuple[str, ...]] = INTENT_SAMPLES):
        self._tokenizer = tokenizer or Tokenizer()
        self._knowledge = knowledge
        self._samples = samples
        self._index: Bm25Index | None = None
        self._classifier: IntentClassifier | None = None
        self.memory = UserMemory()

    # ---------- 对外入口 ----------

    def analyze(self, text: str) -> IntentHit | None:
        """返回意图（关键词层已由调用方先行处理，这里只跑统计层）。"""
        self._ensure_ready()
        grams = self._tokenizer.grams(text)
        hits = self._classifier.predict(grams)          # type: ignore[operator]
        return self._classifier.accept(hits)            # type: ignore[operator]

    def lookup(self, text: str) -> KnowledgeHit | None:
        """意图没接住时，按知识语料检索一条能答的话。"""
        self._ensure_ready()
        grams = self._tokenizer.grams(text)
        return self._index.search(grams, text)          # type: ignore[union-attr]

    def lookup_strong(self, text: str) -> KnowledgeHit | None:
        """只要「问法原样命中」或「得分压倒性」的检索结果。

        用途单一：问句要在状态规则之前拿到答案，但不能让一个模棱两可的检索分
        把「我心情不好怎么办」这类句子抢走，所以这里只收高置信。阈值留在本模块，
        调用方不需要知道 BM25 的分数量级。
        """
        found = self.lookup(text)
        if found is None:
            return None
        if found.score >= KNOWLEDGE_STRONG_SCORE:
            return found
        return None

    def topic_of(self, text: str) -> str:
        """取最「稀有且真实」的内容词当话题：越少见越具体，越值得被追问引用。

        先看语料里出现过的（按 df 升序）；都没见过时只从含中文的词里挑最长的，
        否则像「zzz」「qqq」这种噪声会比「火星文」更像话题，反问起来就很空。
        """
        tokens = [t for t in self._tokenizer.tokenize(text)
                  if len(t) >= TOPIC_MIN_LEN and t not in STOPWORDS]
        if not tokens:
            return ""
        self._ensure_ready()
        index = self._index
        freqs = [(t, index.doc_freq(t)) for t in tokens] if index else []
        known = [(df, -len(t), -i, t) for i, (t, df) in enumerate(freqs) if df > 0]
        if known:
            return min(known)[3]
        cjk = [(len(t), -i, t) for i, t in enumerate(tokens) if has_cjk(t)]
        return max(cjk)[2] if cjk else ""

    def is_anaphoric(self, text: str) -> bool:
        """「嗯」「然后呢」这类输入本身无信息量，应顺着上一轮话题追问。"""
        norm = normalize(text)
        return norm in ANAPHORIC_WORDS or (
            len(norm) <= 4 and any(w.startswith(norm) for w in ANAPHORIC_WORDS))

    def is_question(self, text: str) -> bool:
        """用户是在「问一件事」而不是「说自己的状态」。

        只给路由层当一个闸门：求知型句子不得被「我自己怎么样」这类状态规则接住。
        """
        norm = normalize(text)
        return any(mark in norm for mark in QUESTION_MARKS)

    def extract_memory(self, text: str, patterns: tuple[str, ...],
                       max_len: int = 12) -> str:
        """从「我叫张三」「我在写代码」这类句子里抽出冒号后的短宾语。"""
        norm = normalize(text)
        for pattern in patterns:
            pos = norm.find(pattern)
            if pos < 0:
                continue
            tail = norm[pos + len(pattern):].strip()
            tail = tail.rstrip("。，、！？~,.!?;: ").split("，")[0].split("！")[0]
            if tail:
                return tail[:max_len]
        return ""

    def reset(self) -> None:
        self.memory = UserMemory()

    @property
    def backend(self) -> str:
        return self._tokenizer.backend

    # ---------- 惰性构建 ----------

    def _ensure_ready(self) -> None:
        if self._index is None:
            self._index = Bm25Index(self._tokenizer, self._knowledge)
            self._classifier = IntentClassifier(self._tokenizer, self._samples)
            logger.info("local brain ready: tokenizer=%s docs=%d intents=%d",
                        self._tokenizer.backend, self._index.doc_count,
                        len(self._samples))
