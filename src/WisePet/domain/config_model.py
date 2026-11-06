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
        return cls(ai=ai, ai_profiles=profiles, persona=persona, pet=pet,
                   vitals=vitals, reminders=reminders, news=news, cheer=cheer,
                   bubble=bubble, chat=chat, focus=focus, dnd=dnd)


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
