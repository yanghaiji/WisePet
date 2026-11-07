"""产品层纯逻辑：场景包预设 / 成长外观 / 行为因果 / 权限分级。

零三方依赖、零 IO，全部可直接单测；持久化交给 infra，装配交给组装根。
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from .config_model import AppConfig
from .growth import ADULT_LEVEL, TEEN_LEVEL

#: 一键场景包：id -> (菜单文案, 一句话说明)。
#: 用 replace 组合既有配置，绝不重建 AppConfig（重建会把表单外的参数打回默认值）。
SCENE_PRESETS: dict[str, tuple[str, str]] = {
    "deep_work": ("深度工作", "25 分钟专注、只留喝水/久坐提醒，压低打扰预算"),
    "healthy": ("健康作息", "喝水 30 分钟、久坐 45 分钟，鼓励与周报都开着"),
    "daily_review": ("每日复盘", "新闻 + 鼓励 + 周报齐全，放宽聊天驻留但收紧推送"),
}


def scene_ids() -> tuple[str, ...]:
    return tuple(SCENE_PRESETS)


def scene_label(scene_id: str) -> str:
    entry = SCENE_PRESETS.get(scene_id)
    return entry[0] if entry else ""


def scene_description(scene_id: str) -> str:
    entry = SCENE_PRESETS.get(scene_id)
    return entry[1] if entry else ""


def apply_scene(config: AppConfig, scene_id: str) -> AppConfig:
    """把一组散落设置打包成一次可撤销的配置替换；未知 id 原样返回。

    场景包只改「节奏与开关」，从不碰 ai / api_key / persona / pet / vitals——
    用户自己配的大模型和人设必须原封不动。
    """
    if scene_id not in SCENE_PRESETS:
        return config
    cfg = config
    if scene_id == "deep_work":
        return replace(
            cfg,
            news=replace(cfg.news, enabled=False),
            cheer=replace(cfg.cheer, enabled=False),
            focus=replace(cfg.focus, default_min=25.0),
            reminders=replace(
                cfg.reminders,
                water=replace(cfg.reminders.water, enabled=True, interval_min=45.0),
                sedentary=replace(cfg.reminders.sedentary, enabled=True,
                                  interval_min=50.0)),
            quiet=replace(cfg.quiet, daily_limit=6, per_source_limit=2),
            weekly=replace(cfg.weekly, enabled=False),
        )
    if scene_id == "healthy":
        # 恢复时把过短的推送间隔拉回 sane 值：否则鼓励 1 分钟一条，
        # 几分钟就把单来源额度打满，用户会觉得「刚恢复又没弹窗了」。
        # 只抬高、不压低，尊重用户自己设过的更长间隔。
        return replace(
            cfg,
            news=replace(cfg.news, enabled=True,
                         interval_min=max(30.0, float(cfg.news.interval_min))),
            cheer=replace(cfg.cheer, enabled=True,
                          interval_min=max(30.0, float(cfg.cheer.interval_min))),
            reminders=replace(
                cfg.reminders,
                water=replace(cfg.reminders.water, enabled=True, interval_min=30.0),
                sedentary=replace(cfg.reminders.sedentary, enabled=True,
                                  interval_min=45.0)),
            quiet=replace(cfg.quiet, daily_limit=12, per_source_limit=4, muted=False),
            weekly=replace(cfg.weekly, enabled=True),
        )
    return replace(
        cfg,
        news=replace(cfg.news, enabled=True),
        cheer=replace(cfg.cheer, enabled=True),
        chat=replace(cfg.chat, idle_hide_s=60.0),
        quiet=replace(cfg.quiet, daily_limit=8, per_source_limit=3),
        weekly=replace(cfg.weekly, enabled=True),
    )


@dataclass(frozen=True)
class GrowthDecoration:
    """等级带来的可见外观差异：整体缩放 + 头顶光环 + 等级徽章。

    不改素材文件（素材生产是已知瓶颈），在 PetWindow 绘制帧时叠加。
    """

    scale: float = 1.0
    ring: str = ""
    badge: str = ""

    @property
    def visible(self) -> bool:
        return bool(self.ring or self.badge) or self.scale != 1.0


def growth_decoration(level: int, enabled: bool = True) -> GrowthDecoration:
    if not enabled:
        return GrowthDecoration()
    level = max(1, int(level))
    if level >= ADULT_LEVEL:
        return GrowthDecoration(scale=1.12, ring="#E8A45C", badge=f"Lv{level}")
    if level >= TEEN_LEVEL:
        return GrowthDecoration(scale=1.04, ring="#F8D79B", badge=f"Lv{level}")
    return GrowthDecoration(scale=0.92)


@dataclass(frozen=True)
class BehaviorContext:
    """行为因果驱动的只读外部事实：时段 / 专注中 / 勿扰。"""

    hour: int = 12
    focusing: bool = False
    quiet: bool = False


#: 上下文给出的动作建议；None 表示「照旧走轮播 + 概率」，不额外消耗随机数
CONTEXT_REST = "rest"
CONTEXT_STRETCH = "stretch"
CONTEXT_SLEEP = "sleep"


def context_hint(ctx: BehaviorContext | None, is_night: bool) -> str | None:
    """把生活情境翻译成动作倾向：专注/勿扰要安静，清晨伸懒腰，深夜去睡。

    优先级按「用户当下处境」排：专注和勿扰比时段更重要，避免写作业时乱窜。
    """
    if ctx is None:
        return None
    if ctx.focusing or ctx.quiet:
        return CONTEXT_REST
    if is_night:
        return CONTEXT_SLEEP
    if 6 <= ctx.hour <= 9:
        return CONTEXT_STRETCH
    return None


#: 明确的主动推送来源；`system` 不能整类算推送（工具执行反馈也用 system）
PUSH_SOURCES: frozenset[str] = frozenset({
    "reminder", "cheer", "news", "weekly", "growth", "focus",
})

#: 少数服务用 source="system" 但确实是程序主动推送，靠标题兜住
PUSH_TITLES: frozenset[str] = frozenset({
    "本周回忆录", "成长", "开启新的一天", "定时提醒", "加油打气", "喝水提醒", "久坐提醒",
})


def is_push_source(source: str) -> bool:
    return str(source or "").strip().lower() in PUSH_SOURCES


def is_push_notification(source: str, title: str = "") -> bool:
    """组装根用它判定这条 SpeakRequestedEvent 是否属于「程序主动推送」。"""
    return (is_push_source(source)
            or str(title or "").strip() in PUSH_TITLES)
