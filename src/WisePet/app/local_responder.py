"""LocalResponder：未配置 AI Key 时的本地回复引擎（离线也能聊天，PRD M4 / v1.3 小脑升级）。

四段路由（精度从高到低）：
1. 关键词规则（人工整理、零歧义）——但求知型问句例外：状态类规则不得接住它们，
   否则「猫为什么爱睡觉」会被「睡」这个字抢走、答成宠物自己的精力（v1.4 C6）；
2. 朴素贝叶斯意图分类（听懂关键词表覆盖不到的同义说法）；
3. BM25 知识检索（意图没接住、但语料里有答案）；
4. 反射式兜底 + 顺着上一轮话题反问（承认听不懂，但不冷场）。

文案取向（v1.4 C6，用户真机反馈“很智障”）：第一句先接住用户说的内容或情绪，
宠物自己的状态只在用户主动问起时才播报；兜底不复读用户原话，而是给几句真能问的。

设计约束：
- 除可选 jieba 外只用标准库；上下文可注入，完全确定性（同输入同序列）；
- 只读宠物状态做「状态感知」，不修改任何聚合（无副作用）；
- 轮换用自增计数取模，不用 random，保证测试可断言具体文案；
- 记忆只在本次会话（你的名字/在忙的事/上一轮话题），不写盘、不上传。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..domain.config_model import DEFAULT_SKIN
from .action_protocol import parse_reminder_command, _pretty_delay
from .corpus import (ACTIVITY_PATTERNS, EXAMPLE_ASKS, EXAMPLE_COUNT, FOLLOW_UPS,
                     GUIDE_LINES, NAME_PATTERNS)
from .dialog_engine import DialogEngine

#: 数值分档阈值（与 VitalsSettings 的阈值语义对齐，此处独立用于话术选择）
MOOD_HIGH = 70.0
MOOD_MID = 40.0
MOOD_LOW = 25.0
HUNGER_HIGH = 70.0
HUNGER_MID = 40.0
HUNGER_LOW = 20.0
ENERGY_HIGH = 70.0
ENERGY_MID = 40.0
ENERGY_LOW = 20.0

#: 夜间时段（与 AppConfig.vitals 默认夜间一致，仅用于话术点缀）
NIGHT_START_HOUR = 23
NIGHT_END_HOUR = 7

#: 能力说明：不再挂在首句后面，而是由「你能做什么」承担（v1.4 C6）
LOCAL_MODE_HINT = "（现在是本地小脑模式，右键→设置里接上 AI 大脑就会变聪明）"

#: 会话内记忆抽取的宾语上限（超过就当没听懂，避免把整句存成名字）
MEMORY_VALUE_MAX_LEN = 12

#: 皮肤→自称（本地小脑不能换了小猴还自称猫）
SPECIES_LABEL = {"cat": "小猫", "dog": "小狗", "monkey": "小猴"}

#: 皮肤→语气词
SPECIES_SOUND = {"cat": "喵", "dog": "汪", "monkey": "叽"}


@dataclass(frozen=True)
class LocalContext:
    """回复所需的只读上下文快照（由组装根注入）。"""

    name: str = "小墨"
    hunger: float = 80.0
    mood: float = 80.0
    energy: float = 80.0
    hour: int = 12
    skin: str = DEFAULT_SKIN

    @property
    def is_night(self) -> bool:
        return self.hour >= NIGHT_START_HOUR or self.hour < NIGHT_END_HOUR

    @property
    def species(self) -> str:
        """当前形象的自称词（未知皮肤回落小猫）。"""
        return SPECIES_LABEL.get(self.skin, SPECIES_LABEL[DEFAULT_SKIN])

    @property
    def sound(self) -> str:
        """当前形象的语气词。"""
        return SPECIES_SOUND.get(self.skin, SPECIES_SOUND[DEFAULT_SKIN])


def _mood_line(ctx: LocalContext) -> str:
    if ctx.mood >= MOOD_HIGH:
        return "开心得尾巴都在打转"
    if ctx.mood >= MOOD_MID:
        return "心情平平，有你陪着就不错"
    if ctx.mood >= MOOD_LOW:
        return "有点提不起劲…"
    return "心情低落得很，想被你摸摸头"


def _hunger_line(ctx: LocalContext) -> str:
    if ctx.hunger >= HUNGER_HIGH:
        return "肚子饱饱的"
    if ctx.hunger >= HUNGER_MID:
        return "有点小饿了"
    if ctx.hunger >= HUNGER_LOW:
        return "饿得前爪都发软，右键投喂我嘛"
    return "饿到没力气说话啦…"


def _energy_line(ctx: LocalContext) -> str:
    if ctx.energy >= ENERGY_HIGH:
        return "精力满满"
    if ctx.energy >= ENERGY_MID:
        return "略有点困"
    if ctx.energy >= ENERGY_LOW:
        return "眼皮已经开始打架"
    return "困成一团了，让我睡五分钟"


class LocalResponder:
    """关键词规则 + 统计意图 + 知识检索的话术引擎（状态感知）。"""

    def __init__(self, context_provider: Callable[[], LocalContext],
                 engine: DialogEngine | None = None,
                 on_remind: Callable[[object], None] | None = None):
        self._context = context_provider
        self._engine = engine if engine is not None else DialogEngine()
        self._on_remind = on_remind      # 本地模式解析出定时提醒时回调（组装根注入）
        self._turn = 0
        # 命中顺序即优先级：先强信号（问候/致谢/道别），再情绪信号，最后泛化词。
        # stress 必须在 feel 之前：「我心情不好」里同时有「心情」，
        # 让 feel 先接住就会答成宠物自己的心情（真机反馈的「抢话题」）。
        self._rules: tuple[tuple[str, tuple[str, ...], Callable[[str, LocalContext], str]], ...] = (
            ("greet", GREET_WORDS, self._greet),
            ("thanks", THANKS_WORDS, self._thanks),
            ("bye", BYE_WORDS, self._bye),
            ("who", WHO_WORDS, self._who),
            ("stress", STRESS_WORDS, self._stress),
            ("feel", FEEL_WORDS, self._feel),
            ("work", WORK_WORDS, self._work),
            ("food", FOOD_WORDS, self._food),
            ("sleep", SLEEP_WORDS, self._sleep),
            ("praise", PRAISE_WORDS, self._praise),
            ("insult", INSULT_WORDS, self._insult),
            ("love", LOVE_WORDS, self._love),
            ("joke", JOKE_WORDS, self._joke),
            ("story", STORY_WORDS, self._story),
            ("advice", ADVICE_WORDS, self._advice),
            ("time", TIME_WORDS, self._time),
            ("weather", WEATHER_WORDS, self._weather),
            ("help", HELP_WORDS, self._help),
            ("remind", REMIND_WORDS, self._remind),
            ("ai", AI_WORDS, self._ai_help),
        )
        self._handlers = {name: handler for name, _, handler in self._rules}
        #: 上一次 reply() 命中的路由名（未命中为 fallback），供上层日志区分“哪条规则答的”
        self.last_intent: str = ""

    # ---------- 对外入口 ----------

    def reply(self, text: str) -> str:
        """按四段路由返回一条回复；未命中走反射式兜底。

        首句不再挂能力说明：真机反馈“第一句就告诉我这是本地模式”最像客服机器人，
        说明改由「你能做什么」与兜底话术承担。
        """
        self._turn += 1
        ctx = self._context()
        normalized = str(text or "").strip().lower()
        self._learn(normalized)
        name, answer = self._route(normalized, ctx)
        self.last_intent = name
        return answer

    def reset(self) -> None:
        """会话清空时一并回到初始状态（话题与记忆也清掉）。"""
        self._turn = 0
        self.last_intent = ""
        self._engine.reset()

    def matched_rule(self, text: str) -> str:
        """返回命中的路由名（未命中为 "fallback"），不写记忆。

        诊断用：要“刚才那条是谁答的”就读 last_intent，不要重跑本方法（上一轮已经
        写过话题记忆，重跑可能路由到不同分支）。
        """
        normalized = str(text or "").strip().lower()
        return self._route(normalized, self._context(), observe=True)[0]

    # ---------- 路由 ----------

    def _route(self, text: str, ctx: LocalContext, observe: bool = False) -> tuple[str, str]:
        """返回 (路由名, 回复文案)。observe=True 时不写记忆，供诊断复用同一条链路。"""
        given = self._engine.extract_memory(text, NAME_PATTERNS, MEMORY_VALUE_MAX_LEN)
        if given:
            if not observe:
                self._engine.memory.remember(user_name=given)
            return "name", self._on_name(given, ctx)
        name, handler = self._find_keyword(text)
        if handler is not None:
            # 求知型问句不得被「报宠物自己状态」的规则接住：先问知识层能不能接
            if name in SELF_STATE_RULES and self._engine.is_question(text):
                asked = self._engine.lookup_strong(text)
                if asked is not None:
                    answer = self._answer_knowledge(asked.entry.answers, ctx)
                    self._note("knowledge", text, observe, topic=asked.entry.topic)
                    return "knowledge", answer
            # 先生成再记话题：部分话术要引用「上一轮」的话题，先记会被当轮盖掉
            answer = handler(text, ctx)
            self._note(name, text, observe)
            return name, answer
        hit = self._engine.analyze(text)
        if hit is not None and hit.intent in self._handlers:
            answer = self._handlers[hit.intent](text, ctx)
            self._note(hit.intent, text, observe)
            return hit.intent, answer
        if self._engine.is_anaphoric(text):
            follow = self._follow_up(ctx)
            if follow is not None:
                return "follow_up", follow
        found = self._engine.lookup(text)
        if found is not None:
            answer = self._answer_knowledge(found.entry.answers, ctx)
            self._note("knowledge", text, observe, topic=found.entry.topic)
            return "knowledge", answer
        answer = self._fallback(text, ctx)
        self._note("", text, observe)
        return "fallback", answer

    def _on_name(self, given: str, ctx: LocalContext) -> str:
        """用户报名字：用可验证的应答证明真的接住了（只存本次会话，不写盘）。"""
        return self._pick((
            f"{given}——我默念了两遍，记住了，以后就这么叫你。",
            f"{given}你好呀，我是{ctx.name}，从今天起我就是你专属的{ctx.species}。",
            f"收到，{given}。你说的话我只存本次会话，退出就忘，不在磁盘上留底。",
        ))

    def _note(self, intent: str, text: str, observe: bool, topic: str = "") -> None:
        if observe:
            return
        # 寒暄类意图没有「用户话题」：把「你好」记成话题，下一轮兜底就会假装听到了它
        if intent in PHATIC_INTENTS:
            self._engine.memory.note(intent, "")
            return
        # 用户嘴里的实词比语料条目名更具体：记「工作」而不是「决策」，追问才不会反问空处
        self._engine.memory.note(intent, self._engine.topic_of(text) or topic)

    def _learn(self, text: str) -> None:
        """从用户话里抽名字与正在做的事（只存本次会话）。"""
        name = self._engine.extract_memory(text, NAME_PATTERNS, MEMORY_VALUE_MAX_LEN)
        activity = self._engine.extract_memory(text, ACTIVITY_PATTERNS, MEMORY_VALUE_MAX_LEN)
        if name or activity:
            self._engine.memory.remember(name, activity)

    def _find_keyword(self, text: str) -> tuple[str, Callable[[str, LocalContext], str] | None]:
        """按表序取首个命中规则；关键词已小写，输入也已归一。"""
        for name, words, handler in self._rules:
            if any(w in text for w in words):
                return name, handler
        return "", None

    def _pick(self, pool: tuple[str, ...]) -> str:
        """确定性轮换：同一会话内逐条循环，不会连续重复。"""
        return pool[self._turn % len(pool)]

    def _matter(self, text: str) -> str:
        """把用户话里的实词做成可引用的主语，没抽到就说「这件事」。

        回复里先说出一句「你说的是这事」，是“它真听到了”最便宜也最有效的证据。
        """
        topic = self._engine.topic_of(text)
        # 「我该」「你得」这类由 jieba 切出来的人称+能愿词不是事情的主语，不能当话题引用
        if not topic or topic in VAGUE_MATTERS or topic[0] in PERSON_PRONOUNS:
            return "这件事"
        return f"「{topic}」"

    def _examples(self) -> str:
        """兜底时报几句语料里真能答的问法：承认听不懂，但不让用户对着空气猜。"""
        pool = EXAMPLE_ASKS
        if not pool:
            return "换个说法我可能就接住了。"
        start = self._turn % len(pool)
        picked = list(pool[start:start + EXAMPLE_COUNT])
        if len(picked) < EXAMPLE_COUNT:
            picked += list(pool[:EXAMPLE_COUNT - len(picked)])
        return "你可以试试问：" + "、".join(f"「{ask}」" for ask in picked) + "。"

    # ---------- 多轮：追问与知识 ----------

    def _follow_up(self, ctx: LocalContext) -> str | None:
        """用户只「嗯」「然后呢」时，把上一轮话题接回去；没话题就交还给下层。"""
        topic = self._engine.memory.last_topic
        if not topic:
            return None
        template = self._pick(FOLLOW_UPS)
        return template.format(topic=topic, name=ctx.name)

    def _answer_knowledge(self, answers: tuple[str, ...], ctx: LocalContext) -> str:
        base = self._pick(answers)
        # 低落状态时知识也要带点温度，不能像百科词条
        if ctx.mood < MOOD_MID and "…" not in base:
            return f"{base}（今天不容易，说完这条你先歇口气）"
        return base

    # ---------- 各规则实现 ----------

    def _greet(self, text: str, ctx: LocalContext) -> str:
        """寒暄：先应人，不报状态——「你一打招呼它就报菜名」是上一版最像机器的地方。"""
        mem = self._engine.memory
        lead = f"{mem.user_name}，" if mem.user_name else ""
        tail = f"你刚说在{mem.activity}，现在腾出手了？" if mem.activity else ""
        return self._pick((
            f"{ctx.sound}～{lead}你来啦。我一直蹲在右下角，就等你点我一下。{tail}",
            f"{lead}你好呀。今天想聊两句，还是需要个安静的伴？{tail}",
            f"{lead}在的在的。你先说，我把耳朵贴过来。{tail}",
            ("这么晚了还没休息？我陪你一会儿，但你得早点睡。"
             if ctx.is_night else "早上好。今天先做成一件就行，剩下的我帮你数着。"),
        ))

    def _who(self, text: str, ctx: LocalContext) -> str:
        mem = self._engine.memory
        lead = f"{mem.user_name}，" if mem.user_name else ""
        return self._pick((
            f"我是{ctx.name}呀，一只住在桌面上的{ctx.species}，会走动、会提醒你喝水。",
            f"{lead}我是{ctx.name}，你的桌面{ctx.species}。怎么，我不被认识了？",
        ))

    def _feel(self, text: str, ctx: LocalContext) -> str:
        return f"{_mood_line(ctx)}，饱食度方面{_hunger_line(ctx)}，精力嘛{_energy_line(ctx)}。"

    def _food(self, text: str, ctx: LocalContext) -> str:
        return self._pick((
            f"我{_hunger_line(ctx)}。想逗我玩就右键选「投喂」，我会吃得很快。",
            f"小鱼干！我 {_hunger_line(ctx)}，你一说我就馋了。",
        ))

    def _sleep(self, text: str, ctx: LocalContext) -> str:
        if ctx.is_night:
            return f"夜深啦，我{_energy_line(ctx)}，我们一起早点睡好不好？"
        return f"我{_energy_line(ctx)}。你累了就去休息，我在桌面上等你。"

    def _praise(self, text: str, ctx: LocalContext) -> str:
        return self._pick((
            f"{ctx.sound}～被你说得我耳朵都竖起来了。你今天是不是心情不错？",
            "嘿嘿，再摸摸我的头心情会更好哦（右键可以「抚摸」）。",
            "你才好呢。我每天就守在你打字时看得见的这个角落。",
        ))

    def _insult(self, text: str, ctx: LocalContext) -> str:
        return self._pick((
            "呜…被说笨笨的，心情掉了一点点。",
            "我才不笨！只是还没连上 AI 大脑而已。",
            "你这样说我会难过的，摸摸我好不好。",
        ))

    def _love(self, text: str, ctx: LocalContext) -> str:
        return self._pick((
            "蹭蹭你～（这一蹭心情就满回来了）",
            "我也喜欢你呀，最喜欢坐在你打字时看得见的地方。",
            "rua 我 lightly 一点，胡须会被你摸歪的。",
        ))

    def _joke(self, text: str, ctx: LocalContext) -> str:
        return self._pick((
            "从前有只猫，它叫喵喵。后来它改名叫「喵喵喵」，因为话变多了。",
            "桌面上的文件为什么不会掉下去？因为都被我踩住了呀。",
            "我问小鱼冷不冷，它说冷，所以它住在「水逆」里。",
            "我数了三下就把注意力弄丢了——别问，问就是猫。",
        ))

    def _time(self, text: str, ctx: LocalContext) -> str:
        period = _PERIODS[min(ctx.hour // 3, len(_PERIODS) - 1)]
        return (f"我这边看到大约是 {period} {ctx.hour} 点整，"
                f"准确时间还得问你的系统时钟。")

    def _weather(self, text: str, ctx: LocalContext) -> str:
        return self._pick((
            "我住在屏幕里，看不到窗外……不过你说冷，我就把耳朵贴过来。",
            "天气我不知道，但我知道你多久没起身了——要不要喝口水？",
        ))

    def _help(self, text: str, ctx: LocalContext) -> str:
        return (f"我能做这些：左键点我聊天、拖我到处走、右键可以投喂/抚摸/设置。"
                f"我还会自己走动、犯困、提醒喝水久坐。{LOCAL_MODE_HINT}")

    def _remind(self, text: str, ctx: LocalContext) -> str:
        # 「5分钟后提醒我开会」这类排期说法：本地正则能接就直接办
        action = parse_reminder_command(text)
        if action is not None and self._on_remind is not None:
            self._on_remind(action)
            return f"好，{_pretty_delay(action.delay_min)}后提醒你「{action.text}」"
        return ("可以说「5分钟后提醒我开会」我就给你计时；"
                "喝水和久坐提醒默认开着（45/60 分钟），右键→设置里可以改。")

    def _ai_help(self, text: str, ctx: LocalContext) -> str:
        return ("右键→设置…里填 Base URL、API Key、Model，点「测试连接」再保存，"
                "我就能接上真正的大脑啦。Key 会做密文显示，日志里也会脱敏。")

    def _stress(self, text: str, ctx: LocalContext) -> str:
        """用户内容型：第一句必须接住他说的内容，不报宠物自己的状态。"""
        mem = self._engine.memory
        lead = f"{mem.user_name}，" if mem.user_name else ""
        matter = self._matter(text)
        back = f"你那边还在{mem.activity}，叠在一起谁都会崩。" if mem.activity else ""
        return self._pick((
            f"{lead}{matter}压在你身上有一阵了吧？先说给我听——是那个人，还是量太大。{back}",
            f"{lead}我在。{matter}不用现在就解决，你先骂两句也行，"
            "本地小脑连网都不上，真的没有第三人。",
            f"{lead}难受的时候先给它起个名字：是委屈、是疲惫，还是慌？"
            "说清了我才知道该递水还是递毯子。",
            f"{matter}换个时间点你可能觉得没什么，今天就是扛不住——那也是允许的。{back}",
        ))

    def _work(self, text: str, ctx: LocalContext) -> str:
        """接住「他在做什么」，再给一个能马上执行的动作。"""
        act = self._engine.memory.activity or self._engine.topic_of(text) or "手上那件事"
        return self._pick((
            f"{act}……那你今天节奏挺满的。把我放在看得见的角落，我帮你盯着喝水和起身。",
            f"我把「{act}」记下了。要不要我先按 25 分钟陪你走一段？",
            "先做最小的一步：把文档打开、把最难那段写成一句话。剩下的我陪你磨。",
            f"{act}是卡住了，还是单纯量大？卡住我陪你拆，量大我帮你排顺序。",
        ))

    def _story(self, text: str, ctx: LocalContext) -> str:
        return self._pick(STORY_POOL)

    def _advice(self, text: str, ctx: LocalContext) -> str:
        # 只引用本轮抽到的实词：拿上一轮的话题套到新句子上，
        # 就会说出「「消息」你能把选项说具体一点吗」这种前言不搭后语的话
        matter = self._matter(text)
        return self._pick((
            f"{matter}——你先把两边各自的坏处说给我听，人对自己想选的那边总会美化。",
            f"{matter}先分清「可逆」和「不可逆」：可逆的选哪个都行，"
            "不可逆的多花一晚去想。",
            f"{matter}你能把选项说具体一点吗？只说『好难』我接不住，"
            "说清了两边我就能陪你一项项比。",
        ))

    def _thanks(self, text: str, ctx: LocalContext) -> str:
        return self._pick((f"不客气{ctx.sound}～有事随时叫我。",
                           "为你待机是我的工作呀。", "嘿嘿，我继续守着。"))

    def _bye(self, text: str, ctx: LocalContext) -> str:
        return self._pick((
            "要走了吗？我缩在右下角等你回来。",
            "拜拜～记得喝水，我会提醒你的。",
            "好嘛，我在这儿不打扰你，想我就点我一下。",
        ))

    def _fallback(self, text: str, ctx: LocalContext) -> str:
        """四段都没接住：先给路标，不复读用户原话。

        上一版会把「asdfgh」这类无实词输入原样念一遍，看着像没听懂还在复读——
        那是真机反馈里最“智障”的一处。
        """
        topic = self._engine.topic_of(text)
        if topic:
            return f"我听到的是「{topic}」，但这句我接不住。{self._examples()}"
        return f"{ctx.sound}……这句我没听懂。{self._examples()}{self._pick(GUIDE_LINES)}"


#: 寒暄类意图：不记录话题，否则上一轮的「你好」会被当成下一轮听到的事
PHATIC_INTENTS = frozenset({
    "greet", "thanks", "bye", "who", "praise", "insult", "love", "help", "remind", "ai",
})

#: 抽到这些词时不配当「压在你身上那件事」的主语：它们太泛，
#: 引用出来「「心情」压在你身上有一阵了吧？」就像根本没听懂（v1.4 C6）。
VAGUE_MATTERS = frozenset({"心情", "情绪", "状态", "感觉", "压力", "心态", "事情", "那件事",
                           "不好", "不开心", "不爽", "太累", "好难", "烦"})

#: 人称代词开头的一律不当话题主语：「我该」「你得」引用出来就不是人话了
PERSON_PRONOUNS = "我你他她它咱您"

#: 自我/状态类规则：它们的回复是「报宠物自己的情况」，遇到求知型问句要让给知识层。
#: stress/work/advice 不在其中——「我心态崩了怎么办」是在找情绪承接与对策，
#: 不是求知，被知识层抢走就会变成背百科。这三类只靠 SELF_STATE_RULES 排除在外。
SELF_STATE_RULES = frozenset({
    "greet", "thanks", "bye", "who", "feel", "food", "sleep", "praise", "insult",
    "love", "joke", "story", "time", "weather", "help", "remind", "ai",
})


# --------------------------------------------------------------------------
# 关键词表（放在类后避免前向引用；命中顺序按 _rules 的排列）
# 注：英文短词一律带尾空格，避免 "hi" 命中 "this"、"ai" 命中 "again" 这类误匹配。
# --------------------------------------------------------------------------

#: 时段名（按 3 小时一档，索引 = hour // 3，0~23 点共 8 档）
_PERIODS = ("凌晨", "清晨", "早上", "上午", "中午", "下午", "傍晚", "晚上")

GREET_WORDS = ("你好", "您好", "hello", "hi ", "嗨", "哈喽", "早上好", "晚上好", "在吗")
WHO_WORDS = ("你是谁", "叫什么", "名字", "自我介绍", "什么宠物", "你是啥")
FEEL_WORDS = ("怎么样", "心情", "开心吗", "还好吗", "感觉", "舒不舒服", "过得好")
STRESS_WORDS = ("压力", "焦虑", "烦死", "崩溃", "emo", "心态崩", "心态", "难过", "累死",
                "被骂", "批评", "糟透", "低落", "郁闷", "生气", "委屈",
                "心情不好", "心情差", "心情糟", "很不开心")
WORK_WORDS = ("开会", "加班", "写代码", "改bug", "赶方案", "考试", "论文", "复习",
              "学习", "报表", "上班", "干活", "在忙", "周报", "汇报", "赶工")
FOOD_WORDS = ("饿", "吃", "投喂", "喂", "饭", "零食", "好吃的", "小鱼干", "口水")
SLEEP_WORDS = ("困", "睡", "休息", "累", "熬夜", "失眠")
PRAISE_WORDS = ("可爱", "好看", "漂亮", "喜欢", "棒", "乖", "赞", "好猫", "爱你")
INSULT_WORDS = ("笨", "蠢", "讨厌", "滚", "丑", "没用", "烦", "白吃")
LOVE_WORDS = ("亲亲", "抱抱", "摸摸", "rua", "贴贴")
JOKE_WORDS = ("笑话", "讲个", "无聊", "好玩", "逗", "段子")
STORY_WORDS = ("讲故事", "讲个故事", "冷知识", "说说话", "聊聊天", "陪陪我",
               "说点什么", "有意思的事")
ADVICE_WORDS = ("建议", "怎么办", "怎么选", "该不该", "要不要", "拿个主意", "帮我决定")
TIME_WORDS = ("几点", "时间", "现在")
WEATHER_WORDS = ("天气", "下雨", "下雪", "温度", "冷", "热")
HELP_WORDS = ("能做什么", "功能", "会什么", "帮助", "怎么用", "有什么用", "能干")
REMIND_WORDS = ("提醒", "喝水", "久坐", "起身")
AI_WORDS = ("api", "key", "模型", "配置", "大脑", "人工智能", "连不上", "接不上", "智能")
THANKS_WORDS = ("谢谢", "感谢", "3q", "thx", "辛苦")
BYE_WORDS = ("再见", "拜拜", "走了", "下次", "下班", "告辞")

#: 讲故事/冷知识轮换池（确定性，不接网）
STORY_POOL = (
    "从前有只猫，它叫喵喵。后来改名叫「喵喵喵」，因为话变多了。",
    "猫闻东西时会半张着嘴，那不是嫌弃你，是在用犁鼻器「尝」气味。",
    "你知道猫一天要睡 12 到 16 小时吗？它们的呼噜声频率刚好能促进软组织修复，"
    "所以一边睡觉一边给自己做理疗。",
    "桌上那个纸盒子对我们来说是安全屋：四面有遮挡，温度还高。"
    "跟你把工位围成一圈是一个道理。",
)
