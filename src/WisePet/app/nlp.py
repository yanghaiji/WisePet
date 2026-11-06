"""轻量 NLP 原语：分词 + n-gram + 停用词（PRD M4 本地小脑 v1.3）。

分层约束：只依赖标准库。jieba 是「装了就用、没装就退」的可选加速器：
- 必须惰性 import（词典首次加载实测 0.62~0.67s，绝不能进冷启动路径）；
- 只跑在 chat worker 线程里（组装根保证），UI 线程零阻塞；
- import 失败/未安装 → 回退内置 CJK bigram 分词，功能不崩、测试不依赖它。
"""
from __future__ import annotations

import logging
import re
from typing import Callable, Iterable

logger = logging.getLogger(__name__)

#: 分词粒度用的领域词（jieba 默认词典里没有，不加会被切碎）
DOMAIN_WORDS = ("小鱼干", "桌宠", "投喂", "摸摸头", "气泡框", "久坐", "本地小脑", "大模型")

#: 语气/功能词 + 高频修饰词 + 疑问组合词：参与长度统计但不参与相似度打分。
#: 这里必须含「有点/一直/真的很」这类修饰词，否则它们会以高 idf 顶起检索得分，
#: 把「我心态有点崩」错当成「有点孤单」那条知识（实测踩过）；
#: 也必须含「该不该/要不要/我想」这类疑问组合，否则它们会被当成话题，
#: 反问出一句「关于「该不该」——」的空话；
#: 还必须含「这个/那些」这类指代，否则它们会被当成话题引用出来（v1.4 C6）。
#: 注：「不好/不开心」这类评价词**不进停用词**——它们是「心情不好」那条知识的
#: 主要检索手段，拉黑后知识层会接不住；它们在展示层由 VAGUE_MATTERS 处理。
STOPWORDS = frozenset(
    "的 了 吗 呢 啊 呀 嘛 哦 嗯 吧 是 我 你 他 她 它 们 这 那 就 都 很 还 也 不 和 与 及 "
    "有 个 一 把 被 给 对 为 以 等等 在 到 要 会 能 可以 什么 怎么 怎样 多少 哪 儿 上 下 里 "
    "有点 一直 真的 只是 现在 已经 刚才 然后 还有 给我 帮我 你的 我的 要是 如果 因为 所以 就是 "
    "还是 反正 应该 可能 这个 那个 这些 那些 这么 那么 人家 "
    "该不该 要不要 是不是 能不能 有没有 怎么办 为什么 怎么样 哪个 哪些 一下 一点 一些 "
    "我想 我要 你能 你会 不该 不会 不要 问问 啥 "
    "a an the is are was were be do does did you i it this that and or of to in for on "
    "me my your".split()
)

_CJK_RANGES = "\u4e00-\u9fff"
_SEGMENT_RE = re.compile(rf"[{_CJK_RANGES}]+|[a-z0-9]+")
#: 至少含一个字/数字才当词元（jieba 会把逗号、全角标点也切成 token）
_WORD_CHAR_RE = re.compile(r"\w")


def normalize(text: str) -> str:
    """归一输入：去首尾空白 + 转小写（关键词表与小写语料对齐）。"""
    return str(text or "").strip().lower()


def is_cjk(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff"


def has_cjk(text: str) -> bool:
    """字串里是否含中日韩字符（用来判断一个词能不能当话题）。"""
    return any(is_cjk(ch) for ch in str(text or ""))


def bigram_split(text: str) -> list[str]:
    """零依赖回退分词：中文按相邻二字切，英文/数字整段保留。

    召回比 jieba 宽（"心情不好" 产出 心情/情不/不好），小语料下够用且确定性。
    """
    tokens: list[str] = []
    for seg in _SEGMENT_RE.findall(str(text or "").lower()):
        if len(seg) == 1 or not is_cjk(seg[0]):
            tokens.append(seg)
        else:
            tokens.extend(seg[i:i + 2] for i in range(len(seg) - 1))
    return tokens


def _load_jieba() -> Callable[[str], list[str]] | None:
    """尝试加载 jieba 并把领域词并入词典；失败返回 None（调用方回退 bigram）。"""
    try:
        import jieba
    except Exception as exc:  # noqa: BLE001 - 未安装/依赖异常一律降级，不拖垮聊天
        logger.info("jieba unavailable (%s), using built-in bigram tokenizer", exc)
        return None
    try:
        if hasattr(jieba, "setLogLevel"):
            jieba.setLogLevel(60)          # jieba 的 dump/load 日志会污染我们的日志文件
        for word in DOMAIN_WORDS:
            jieba.add_word(word)
    except Exception:  # noqa: BLE001 - 词典定制失败不影响主分词
        logger.warning("jieba domain dictionary tuning failed", exc_info=True)
    logger.info("tokenizer backend = jieba %s", getattr(jieba, "__version__", "?"))
    return lambda text: list(jieba.cut(text))


class Tokenizer:
    """惰性分词器：首次 tokenize 才决定用 jieba 还是 bigram。

    ``splitter`` 可注入（测试用 bigram 或假分词器，避免为断言付出词典加载成本）。
    """

    def __init__(self, splitter: Callable[[str], Iterable[str]] | None = None):
        self._splitter = splitter
        self._resolved = splitter is not None

    @property
    def backend(self) -> str:
        """当前后端名，供日志与测试断言（jieba / custom / bigram）。"""
        if not self._resolved:
            return "pending"
        return getattr(self, "_backend_name", "custom")

    def tokenize(self, text: str) -> list[str]:
        if not self._resolved:
            self._resolve()
        try:
            raw = list(self._splitter(normalize(text)))   # type: ignore[misc]
        except Exception:  # noqa: BLE001 - 分词器崩了也不能让聊天卡住
            logger.warning("tokenize failed, fallback to bigram", exc_info=True)
            raw = bigram_split(text)
        return [tok for tok in (str(t).strip() for t in raw)
                if tok and _WORD_CHAR_RE.search(tok)]

    def grams(self, text: str) -> list[str]:
        """unigram + 相邻 unigram 拼出的 bigram，停用词不参与拼接。

        同时索引两种粒度是为了「同义表达命中」：语料写「心情不好」，
        用户说「心情太差了」也能搭上。拼接只取两个都是实词的相邻对，
        否则「今天被」「了一」这类虚词碎块会混进语料，让检索谁都像谁。
        """
        tokens = self.tokenize(text)
        grams: list[str] = []
        for i, tok in enumerate(tokens):
            if tok in STOPWORDS:
                continue
            grams.append(tok)
            if i + 1 < len(tokens) and tokens[i + 1] not in STOPWORDS:
                grams.append(tokens[i] + tokens[i + 1])
        return grams

    def _resolve(self) -> None:
        self._resolved = True
        try:
            jieba_cut = _load_jieba()
        except Exception:  # noqa: BLE001 - 词典加载异常绝不能冒到聊天链路上
            logger.warning("tokenizer backend init failed, using bigram", exc_info=True)
            jieba_cut = None
        if jieba_cut is None:
            self._splitter = bigram_split
            self._backend_name = "bigram"
        else:
            self._splitter = jieba_cut
            self._backend_name = "jieba"


def overlap_score(query: list[str], doc: list[str]) -> float:
    """Jaccard 式重合度（长度归一），用于「用户上一句 vs 语料问法」的粗判。"""
    qs, ds = set(query), set(doc)
    if not qs or not ds:
        return 0.0
    return len(qs & ds) / min(len(qs), len(ds))
