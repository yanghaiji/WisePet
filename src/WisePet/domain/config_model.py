"""配置领域模型：分节类型化配置（PRD M7 / 技术方案 §6）。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .ports import AIConfig
from .vitals import DecayRules

DEFAULT_SYSTEM_PROMPT = (
    "你是名叫{name}的桌面宠物，正住在用户的电脑桌面上。"
    "当前状态：{vitals_summary}。"
    "请始终以这只宠物的身份和口吻，用简短（不超过100字）、亲切、俏皮的中文回答。"
)

#: 可选形象皮肤（即 assets/pet/{skin}/ 目录名），首个为默认与回落皮肤
PET_SKINS: tuple[str, ...] = ("cat", "dog", "monkey")
DEFAULT_SKIN = PET_SKINS[0]


def normalize_skin(raw) -> str:
    """皮肤取值归一：非法/空值回落默认皮肤（配置损坏不崩，PRD M7 边界）。"""
    skin = str(raw or "").strip().lower()
    return skin if skin in PET_SKINS else DEFAULT_SKIN


@dataclass
class PersonaConfig:
    name: str = "小墨"
    system_prompt: str = DEFAULT_SYSTEM_PROMPT


@dataclass
class AIProfile:
    """可切换的模型方案：聊天窗下拉一键换脑（api_key 独立保存）。"""
    name: str = "默认"
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"

    @classmethod
    def from_ai(cls, ai) -> "AIProfile":
        """把当前生效的 AIConfig 快照成一个方案（旧配置升级入口）。"""
        return cls(name="默认", base_url=ai.base_url,
                   api_key=ai.api_key, model=ai.model)


@dataclass
class PetParams:
    skin: str = DEFAULT_SKIN          # 形象皮肤（PRD v1.2 C3）
    walk_speed_px_per_s: float = 60.0   # 自主走动速度
    run_speed_px_per_s: float = 150.0   # 小跑速度（PRD v2.0 C11）
    idle_min_s: float = 3.0             # 随机闲置行为最短间隔（PRD v2.0 C11：5→3）
    idle_max_s: float = 9.0             # 随机闲置行为最长间隔（PRD v2.0 C11：15→9）
    walk_max_s: float = 12.0            # 单趟溜达最长时长，到边折返不立即停（PRD v2.0 C11）
    run_trigger_p: float = 0.25         # 开走那一趟是小跑的概率
    stretch_trigger_p: float = 0.2      # 待机时伸懒腰的概率（睡醒必伸）
    stick_to_floor: bool = True         # 贴地：非拖拽时自然落回工作区下沿（PRD v2.0 C10）
    fall_speed_px_per_s: float = 900.0  # 下落末速（关掉贴地则不参与）
    roam: bool = True                   # 自由漫游：不被钉在一条水平线上，可停留在桌面任意高度走/睡
    roam_jump_interval_s: float = 30.0  # 柔和瞬移换地方的间隔秒（0 = 不瞬移）
    max_turns: int = 20                 # 会话保留轮数（PRD US-3）
    click_move_threshold_px: int = 5    # 区分点击与拖拽的位移阈值
    activity: str = "normal"            # 活泼度：quiet / normal / lively，缩放自主行为间隔


@dataclass
class VitalsSettings:
    rules: DecayRules = field(default_factory=DecayRules)
    sad_threshold: float = 25.0     # 心情低于此值进入 SAD
    danger_threshold: float = 15.0  # 任一维度低于此值主动求助
    help_cooldown_s: float = 1800.0  # 主动求助冷却（PRD：≥30min）
    night_start_hour: int = 23
    night_end_hour: int = 7
    save_interval_s: float = 60.0   # 状态周期落盘


@dataclass
class ReminderItem:
    enabled: bool = True
    interval_min: float = 45.0


#: 默认 RSS 源（国内科技/数码向，免 Key 可直接用；可在 config.json 里增删）
DEFAULT_NEWS_SOURCES: tuple[str, ...] = (
    "https://36kr.com/feed",
    "https://www.ithome.com/rss/",
    "https://sspai.com/feed",
)


@dataclass
class NewsSettings:
    """定时新闻播报（RSS 拉标题，配了 AI Key 时摘要后播报）。"""
    enabled: bool = True
    interval_min: float = 60.0
    sources: list[str] = field(default_factory=lambda: list(DEFAULT_NEWS_SOURCES))
    max_headlines: int = 8


@dataclass
class CheerSettings:
    """定时鼓励推送：积极的话 / 古诗名句 / 夸奖，配了 AI 时生成、未配走本地语录。"""
    enabled: bool = True
    interval_min: float = 3.0


@dataclass
class BubbleSettings:
    """通知气泡统一驻留时长：管所有提示（提醒/新闻/鼓励/AI 回复…）。"""
    duration_s: float = 15.0


@dataclass
class ChatSettings:
    """聊天输入条空闲行为：无交互超过 idle_hide_s 秒自动收起（0 = 不自动隐藏）。"""
    idle_hide_s: float = 30.0


@dataclass
class TtsSettings:
    """语音说话：提醒/回复可开口朗读（本地 TTS，Windows+macOS，零上传）。"""
    enabled: bool = False
    volume: int = 100      # 0~100 音量
    rate: int = 0          # -10~10 语速（慢→快），macOS 折算为 speaking rate


@dataclass
class NotificationSettings:
    """通知呈现方式：自绘气泡（陪伴感）或接入系统通知中心（降打扰）。"""
    use_system: bool = False


@dataclass
class WeeklySettings:
    """回忆录：每周指定日基于本地聊天记录生成并推送周报（全本地数据）。"""
    enabled: bool = True
    weekday: int = 6       # 0=周一 … 6=周日，默认周日推送
    hour: int = 20         # 推送时刻（本地小时，0~23）


@dataclass
class GrowthSettings:
    """成长体系：喂食/抚摸/陪伴互动积累 → 等级与形态（幼年→成长→成熟）。"""
    enabled: bool = True


@dataclass
class QuietBudgetSettings:
    """打扰预算：主动推送每日总量 / 单来源上限 + 一键免打扰。

    只管主动推送（提醒/新闻/鼓励/周报/成长/专注/问候）；用户主动发起的
    聊天回复与工具执行反馈不受预算拦截，否则「问了却没回」会被误当成 bug。
    """
    enabled: bool = True
    daily_limit: int = 12
    per_source_limit: int = 4
    muted: bool = False
    overflow_mode: str = "block"   # block 全停 / trickle 降频（不再是整天静默）
    trickle_every: int = 5         # trickle 模式每 N 次拦截放行 1 次


@dataclass
class BehaviorSettings:
    """行为因果驱动：按当前时段 / 专注 / 勿扰选动作，而不是纯随机轮播。"""
    context_driven: bool = True


@dataclass
class MemorySettings:
    """长期记忆与跨日检索：只落本机，不上传（隐私卖点的护城河）。"""
    enabled: bool = True
    search_max_results: int = 50


@dataclass
class ActionSettings:
    """写盘类意图的权限级别：auto 直接执行 / confirm 每次确认 / deny 一律拒绝。"""
    write_permission: str = "confirm"
    remember_allowed: bool = False


@dataclass
class SystemSettings:
    """系统配置：与宠物本身无关的运行时参数，集中在设置窗「系统配置」分区。

    日志级别 / 单文件分割阈值 / 备份保留天数 / 是否另存错误档；
    保存后立即热重建日志 handler，不用重启桌宠。
    """
    log_level: str = "info"          # debug / info / warning / error
    log_max_mb: int = 10             # 单文件上限，MiB
    log_retention_days: int = 3      # 滚动备份保留天数
    log_split_by_level: bool = True  # 是否另存 WARNING+ 错误档


@dataclass
class FocusSettings:
    """番茄钟：默认专注时长，一句话（「来个番茄钟」）与右键菜单都取这个值。"""
    default_min: float = 25.0


@dataclass
class DndSettings:
    """勿扰时段：周期提醒/新闻/鼓励顺延到时段结束；一句话定时提醒不受影响。"""
    enabled: bool = False
    start_hour: int = 12
    end_hour: int = 14


@dataclass
class ReminderSettings:
    water: ReminderItem = field(default_factory=lambda: ReminderItem(True, 45.0))
    sedentary: ReminderItem = field(default_factory=lambda: ReminderItem(True, 60.0))
    snooze_min: float = 5.0        # 勿扰顺延（PRD M6）
    min_interval_min: float = 1.0  # 间隔配置下限（PRD 边界）


@dataclass
class AppConfig:
    ai: AIConfig = field(default_factory=AIConfig)
    ai_profiles: list = field(default_factory=list)
    persona: PersonaConfig = field(default_factory=PersonaConfig)
    pet: PetParams = field(default_factory=PetParams)
    vitals: VitalsSettings = field(default_factory=VitalsSettings)
    reminders: ReminderSettings = field(default_factory=ReminderSettings)
    news: NewsSettings = field(default_factory=NewsSettings)
    cheer: CheerSettings = field(default_factory=CheerSettings)
    bubble: BubbleSettings = field(default_factory=BubbleSettings)
    chat: ChatSettings = field(default_factory=ChatSettings)
    focus: FocusSettings = field(default_factory=FocusSettings)
    dnd: DndSettings = field(default_factory=DndSettings)
    tts: TtsSettings = field(default_factory=TtsSettings)
    notification: NotificationSettings = field(default_factory=NotificationSettings)
    weekly: WeeklySettings = field(default_factory=WeeklySettings)
    growth: GrowthSettings = field(default_factory=GrowthSettings)
    quiet: QuietBudgetSettings = field(default_factory=QuietBudgetSettings)
    behavior: BehaviorSettings = field(default_factory=BehaviorSettings)
    memory: MemorySettings = field(default_factory=MemorySettings)
    actions: ActionSettings = field(default_factory=ActionSettings)
    system: SystemSettings = field(default_factory=SystemSettings)

    def to_dict(self) -> dict:
        data = asdict(self)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "AppConfig":
        """宽松解析：缺失/非法字段回落默认值。"""
        data = data if isinstance(data, dict) else {}
        defaults = cls()

        ai_d = data.get("ai") or {}
        ai = AIConfig(
            base_url=str(ai_d.get("base_url") or defaults.ai.base_url),
            api_key=str(ai_d.get("api_key") or ""),
            model=str(ai_d.get("model") or defaults.ai.model),
            temperature=_num(ai_d.get("temperature"), defaults.ai.temperature),
            timeout_s=_num(ai_d.get("timeout_s"), defaults.ai.timeout_s),
        )
        profiles = _parse_profiles(data.get("ai_profiles"), ai)
        p_d = data.get("persona") or {}
        persona = PersonaConfig(
            name=str(p_d.get("name") or defaults.persona.name),
            system_prompt=str(p_d.get("system_prompt") or defaults.persona.system_prompt),
        )
        pet_d = data.get("pet") or {}
        pet = PetParams(
            skin=normalize_skin(pet_d.get("skin")),
            activity=_choice(pet_d.get("activity"), defaults.pet.activity,
                             ("quiet", "normal", "lively")),
            walk_speed_px_per_s=_num(pet_d.get("walk_speed_px_per_s"), defaults.pet.walk_speed_px_per_s),
            run_speed_px_per_s=_num(pet_d.get("run_speed_px_per_s"), defaults.pet.run_speed_px_per_s),
            idle_min_s=_num(pet_d.get("idle_min_s"), defaults.pet.idle_min_s),
            idle_max_s=_num(pet_d.get("idle_max_s"), defaults.pet.idle_max_s),
            walk_max_s=_num(pet_d.get("walk_max_s"), defaults.pet.walk_max_s),
            run_trigger_p=_prob(pet_d.get("run_trigger_p"), defaults.pet.run_trigger_p),
            stretch_trigger_p=_prob(pet_d.get("stretch_trigger_p"), defaults.pet.stretch_trigger_p),
            stick_to_floor=_bool(pet_d.get("stick_to_floor"), defaults.pet.stick_to_floor),
            fall_speed_px_per_s=_num(pet_d.get("fall_speed_px_per_s"), defaults.pet.fall_speed_px_per_s),
            roam=_bool(pet_d.get("roam"), defaults.pet.roam),
            roam_jump_interval_s=_num(pet_d.get("roam_jump_interval_s"),
                                      defaults.pet.roam_jump_interval_s),
            max_turns=int(_num(pet_d.get("max_turns"), defaults.pet.max_turns)),
            click_move_threshold_px=int(_num(pet_d.get("click_move_threshold_px"),
                                             defaults.pet.click_move_threshold_px)),
        )
        v_d = data.get("vitals") or {}
        r_d = v_d.get("rules") or {}
        vitals = VitalsSettings(
            rules=DecayRules(
                hunger_per_hour=_num(r_d.get("hunger_per_hour"), defaults.vitals.rules.hunger_per_hour),
                mood_per_hour=_num(r_d.get("mood_per_hour"), defaults.vitals.rules.mood_per_hour),
                energy_per_hour=_num(r_d.get("energy_per_hour"), defaults.vitals.rules.energy_per_hour),
                energy_night_factor=_num(r_d.get("energy_night_factor"),
                                         defaults.vitals.rules.energy_night_factor),
                energy_recover_per_hour=_num(r_d.get("energy_recover_per_hour"),
                                             defaults.vitals.rules.energy_recover_per_hour),
                floor=_num(r_d.get("floor"), defaults.vitals.rules.floor),
            ),
            sad_threshold=_num(v_d.get("sad_threshold"), defaults.vitals.sad_threshold),
            danger_threshold=_num(v_d.get("danger_threshold"), defaults.vitals.danger_threshold),
            help_cooldown_s=_num(v_d.get("help_cooldown_s"), defaults.vitals.help_cooldown_s),
            night_start_hour=int(_num(v_d.get("night_start_hour"), defaults.vitals.night_start_hour)),
            night_end_hour=int(_num(v_d.get("night_end_hour"), defaults.vitals.night_end_hour)),
            save_interval_s=_num(v_d.get("save_interval_s"), defaults.vitals.save_interval_s),
        )
        rm_d = data.get("reminders") or {}

        def _item(raw, fallback: ReminderItem) -> ReminderItem:
            raw = raw or {}
            return ReminderItem(
                enabled=bool(raw.get("enabled", fallback.enabled)),
                interval_min=_num(raw.get("interval_min"), fallback.interval_min),
            )

        reminders = ReminderSettings(
            water=_item(rm_d.get("water"), defaults.reminders.water),
            sedentary=_item(rm_d.get("sedentary"), defaults.reminders.sedentary),
            snooze_min=_num(rm_d.get("snooze_min"), defaults.reminders.snooze_min),
            min_interval_min=_num(rm_d.get("min_interval_min"), defaults.reminders.min_interval_min),
        )
        n_d = data.get("news") or {}
        raw_sources = n_d.get("sources")
        news = NewsSettings(
            enabled=_bool(n_d.get("enabled"), defaults.news.enabled),
            interval_min=_num(n_d.get("interval_min"), defaults.news.interval_min),
            sources=[str(s).strip() for s in raw_sources if str(s or "").strip()]
                    if isinstance(raw_sources, list) else list(defaults.news.sources),
            max_headlines=int(_num(n_d.get("max_headlines"), defaults.news.max_headlines)),
        )
        c_d = data.get("cheer") or {}
        cheer = CheerSettings(
            enabled=_bool(c_d.get("enabled"), defaults.cheer.enabled),
            interval_min=_num(c_d.get("interval_min"), defaults.cheer.interval_min),
        )
        b_d = data.get("bubble") or {}
        bubble = BubbleSettings(
            duration_s=max(3.0, _num(b_d.get("duration_s"), defaults.bubble.duration_s)),
        )
        ch_d = data.get("chat") or {}
        chat = ChatSettings(
            idle_hide_s=max(0.0, _num(ch_d.get("idle_hide_s"), defaults.chat.idle_hide_s)),
        )
        f_d = data.get("focus") or {}
        focus = FocusSettings(
            default_min=min(180.0, max(1.0, _num(f_d.get("default_min"),
                                                 defaults.focus.default_min))),
        )
        d_d = data.get("dnd") or {}
        dnd = DndSettings(
            enabled=_bool(d_d.get("enabled"), defaults.dnd.enabled),
            start_hour=int(_num(d_d.get("start_hour"), defaults.dnd.start_hour)) % 24,
            end_hour=int(_num(d_d.get("end_hour"), defaults.dnd.end_hour)) % 24,
        )
        t_d = data.get("tts") or {}
        tts = TtsSettings(
            enabled=_bool(t_d.get("enabled"), defaults.tts.enabled),
            volume=_int_clamp(t_d.get("volume"), defaults.tts.volume, 0, 100),
            rate=_int_clamp(t_d.get("rate"), defaults.tts.rate, -10, 10),
        )
        nt_d = data.get("notification") or {}
        notification = NotificationSettings(
            use_system=_bool(nt_d.get("use_system"), defaults.notification.use_system),
        )
        w_d = data.get("weekly") or {}
        weekly = WeeklySettings(
            enabled=_bool(w_d.get("enabled"), defaults.weekly.enabled),
            weekday=int(_num(w_d.get("weekday"), defaults.weekly.weekday)) % 7,
            hour=int(_num(w_d.get("hour"), defaults.weekly.hour)) % 24,
        )
        g_d = data.get("growth") or {}
        growth = GrowthSettings(
            enabled=_bool(g_d.get("enabled"), defaults.growth.enabled),
        )
        q_d = data.get("quiet") or {}
        quiet = QuietBudgetSettings(
            enabled=_bool(q_d.get("enabled"), defaults.quiet.enabled),
            daily_limit=_int_clamp(q_d.get("daily_limit"), defaults.quiet.daily_limit, 1, 200),
            per_source_limit=_int_clamp(q_d.get("per_source_limit"),
                                        defaults.quiet.per_source_limit, 1, 100),
            muted=_bool(q_d.get("muted"), defaults.quiet.muted),
            overflow_mode=_choice(q_d.get("overflow_mode"),
                                  defaults.quiet.overflow_mode,
                                  ("block", "trickle")),
            trickle_every=_int_clamp(q_d.get("trickle_every"),
                                     defaults.quiet.trickle_every, 2, 50),
        )
        b_d = data.get("behavior") or {}
        behavior = BehaviorSettings(
            context_driven=_bool(b_d.get("context_driven"), defaults.behavior.context_driven),
        )
        m_d = data.get("memory") or {}
        memory = MemorySettings(
            enabled=_bool(m_d.get("enabled"), defaults.memory.enabled),
            search_max_results=_int_clamp(m_d.get("search_max_results"),
                                          defaults.memory.search_max_results, 5, 500),
        )
        a_d = data.get("actions") or {}
        actions = ActionSettings(
            write_permission=_choice(a_d.get("write_permission"),
                                     defaults.actions.write_permission,
                                     ("auto", "confirm", "deny")),
            remember_allowed=_bool(a_d.get("remember_allowed"),
                                   defaults.actions.remember_allowed),
        )
        s_d = data.get("system") or {}
        system = SystemSettings(
            log_level=_choice(s_d.get("log_level"), defaults.system.log_level,
                              ("debug", "info", "warning", "error")),
            log_max_mb=_int_clamp(s_d.get("log_max_mb"),
                                  defaults.system.log_max_mb, 1, 100),
            log_retention_days=_int_clamp(s_d.get("log_retention_days"),
                                          defaults.system.log_retention_days, 1, 30),
            log_split_by_level=_bool(s_d.get("log_split_by_level"),
                                     defaults.system.log_split_by_level),
        )
        return cls(ai=ai, ai_profiles=profiles, persona=persona, pet=pet,
                   vitals=vitals, reminders=reminders, news=news, cheer=cheer,
                   bubble=bubble, chat=chat, focus=focus, dnd=dnd,
                   tts=tts, notification=notification, weekly=weekly, growth=growth,
                   quiet=quiet, behavior=behavior, memory=memory, actions=actions,
                   system=system)


def _parse_profiles(raw, ai: AIConfig) -> list:
    """解析模型方案列表；缺省/损坏时回落单方案（当前 ai 快照，旧配置自动升级）。"""
    profiles: list = []
    for item in (raw if isinstance(raw, list) else []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        base_url = str(item.get("base_url") or "").strip()
        if not name or not base_url:
            continue
        profiles.append(AIProfile(
            name=name, base_url=base_url,
            api_key=str(item.get("api_key") or ""),
            model=str(item.get("model") or ai.model)))
    if not profiles:
        profiles = [AIProfile.from_ai(ai)]
    # 当前生效的 ai 必须在方案列表里有一席：缺失时把当前配置补录进去
    if not any(p.base_url == ai.base_url and p.model == ai.model
               for p in profiles):
        profiles.append(AIProfile.from_ai(ai))
    return profiles


def _num(raw, fallback: float) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(fallback)


def _prob(raw, fallback: float) -> float:
    """概率型配置：非法值回默认，越界钳到 0~1（手改 json 写成 25 也不会“必发”）。"""
    return min(1.0, max(0.0, _num(raw, fallback)))


def _bool(raw, fallback: bool) -> bool:
    """布尔配置：缺键/空值回默认，不把 null 当成 False。"""
    return fallback if raw is None else bool(raw)


def _int_clamp(raw, fallback: int, lo: int, hi: int) -> int:
    """整数配置：非法值回默认，越界钳到 [lo, hi]（手改 json 写超范围也不会失控）。"""
    return min(hi, max(lo, int(_num(raw, fallback))))


def _choice(raw, fallback: str, allowed: tuple[str, ...]) -> str:
    """枚举配置：手改 json 写了白名单外的值一律回默认，避免出现无处理分支。"""
    value = str(raw or "").strip().lower()
    return value if value in allowed else fallback
