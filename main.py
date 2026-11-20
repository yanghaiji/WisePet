"""组装根：infra → app → ui 装配与应用启动（技术方案 §4 / §9 Step 6）。

只有本文件与 ui/ 允许 import PySide6；业务判断全部在 domain 与 app 层。
"""
from __future__ import annotations

import json
import logging
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

def _runtime_root() -> Path:
    """组装根所在目录：源码运行取项目根，冻结后取随包分发的 assets 父目录。

    PyInstaller onedir 的 exe 在 dist/WisePet/（Windows）或
    WisePet.app/Contents/MacOS/（macOS），素材由 tools/package.py 放在
    exe 同级 assets/ 或 Contents/Resources/assets/；sys._MEIPASS 兜底。
    """
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parent
    exe_dir = Path(sys.executable).resolve().parent
    internal = Path(getattr(sys, "_MEIPASS", exe_dir))
    for assets in (internal / "assets", exe_dir / "assets",
                   exe_dir.parent / "Resources" / "assets"):
        if (assets / "pet").is_dir():
            return assets.parent
    return internal


ROOT = _runtime_root()
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QProcess, QTimer, QUrl
from shiboken6 import isValid
from PySide6.QtGui import QCursor, QDesktopServices, QGuiApplication   # noqa: E402
from PySide6.QtWidgets import (                                        # noqa: E402
    QApplication, QDialog, QInputDialog, QMessageBox)

from WisePet.app.action_audit import ActionAudit                       # noqa: E402
from WisePet.app.action_protocol import (                               # noqa: E402
    ACTION_PROMPT_GUIDE,
    CreateFileAction,
    MakePptAction,
    RemindAction,
    PERMISSION_AUTO,
    PERMISSION_DENY,
    action_preview,
    describe_action,
    is_cancel_reminders_command,
    permission_required,
    parse_action,
    parse_daily_reminder_command,
    parse_focus_command,
    parse_reminder_command,
)
from WisePet.app.agent_loop import AgentRunner                          # noqa: E402
from WisePet.app.auto_facts import AutoFactExtractor                     # noqa: E402
from WisePet.app.agent_tools import AgentToolbox                        # noqa: E402
from WisePet.app.agent_loop import wants_image, wants_video              # noqa: E402
from WisePet.app.summary_memory import SummaryMemory, SummaryStore       # noqa: E402
from WisePet.app.cheer_service import CheerService                      # noqa: E402
from WisePet.app.companion_service import CompanionService              # noqa: E402
from WisePet.app.chat_history import ChatHistory                        # noqa: E402
from WisePet.app.chat_service import ChatService                       # noqa: E402
from WisePet.app.data_steward import DataSteward                        # noqa: E402
from WisePet.app.event_bus import EventBus                              # noqa: E402
from WisePet.app.event_triggers import EventTriggerService             # noqa: E402
from WisePet.app.focus_service import FocusLog, FocusService           # noqa: E402
from WisePet.app.knowledge_base import KnowledgeBase                    # noqa: E402
from WisePet.app.memory_timeline import MemoryTimeline                  # noqa: E402
from WisePet.app.task_service import TaskService                        # noqa: E402
from WisePet.app.growth_service import GrowthService                    # noqa: E402
from WisePet.app.local_responder import LocalContext, LocalResponder    # noqa: E402
from WisePet.app.long_term_memory import LongTermMemory                  # noqa: E402
from WisePet.app.quiet_budget import QuietBudget                        # noqa: E402
from WisePet.app.news_service import NewsService                       # noqa: E402
from WisePet.app.one_shot_reminders import OneShotReminders             # noqa: E402
from WisePet.app.pet_controller import PetController                   # noqa: E402
from WisePet.app.reminder_service import ReminderService               # noqa: E402
from WisePet.app.telemetry_service import TelemetryService             # noqa: E402
from WisePet.app.tts_announcer import TtsAnnouncer                      # noqa: E402
from WisePet.app.weekly_report import WeeklyReportService              # noqa: E402
from WisePet.domain.chat_record import day_key                          # noqa: E402
from WisePet.domain.chat_session import ChatSession                    # noqa: E402
from WisePet.domain.events import (                                    # noqa: E402
    PHASE_ERROR,
    PHASE_IDLE,
    PHASE_THINKING,
    BehaviorChangedEvent,
    ChatErrorEvent,
    ChatReplyChunkEvent,
    ChatReasoningEvent,
    ChatStateChangedEvent,
    ChatTurnRecordedEvent,
    KIND_SEDENTARY,
    KIND_WATER,
    MediaTaskEvent,
    ReminderDueEvent,
    SoftJumpRequestedEvent,
    HotkeyChatRequestedEvent,
    SpeakRequestedEvent,
    UpdateCheckResultEvent,
    UpdateDownloadedEvent,
)
from WisePet.domain.companion import context_phrase                    # noqa: E402
from WisePet.domain.model_capability import (                          # noqa: E402
    capability_hint, infer_tier)
from WisePet.domain.persona_anchor import anchor_prompt                 # noqa: E402
from WisePet.domain.companion_depth import (                           # noqa: E402
    discoverability_line, memory_visibility_line, mood_tone_line,
    name_first_line, schedule_empathy_line, sulk_line)
from WisePet.app.care_nudge import nudge_line, should_nudge             # noqa: E402
from WisePet.domain.version import read_current_version                 # noqa: E402
from WisePet.app.situation_service import SituationService               # noqa: E402
from WisePet.infra.situation_store import SituationStore                 # noqa: E402
from WisePet.infra.update_service import UpdateService                  # noqa: E402
from WisePet.domain.task_protocol import is_done_signal, parse_task_command  # noqa: E402
from WisePet.domain.config_model import AppConfig, normalize_purpose    # noqa: E402
from WisePet.domain.ports import PURPOSE_IMAGE, PURPOSE_VIDEO           # noqa: E402
from WisePet.domain.pet import Pet                                     # noqa: E402
from WisePet.domain.product import (                                   # noqa: E402
    BehaviorContext, apply_scene, growth_decoration, is_push_notification,
    scene_label,
)
from WisePet.domain.position import PositionVO                         # noqa: E402
from WisePet.domain.state_machine import BehaviorState                  # noqa: E402
from WisePet.domain.errors import AIGatewayError                        # noqa: E402
from WisePet.domain.vitals import VitalsVO                              # noqa: E402
from WisePet.infra.sqlite_chat_repo import SqliteChatRepository         # noqa: E402
from WisePet.infra.ai_stores import AIUsageStore, SharedAIStore        # noqa: E402
from WisePet.infra.config_repo import (                               # noqa: E402
    JsonConfigRepository, default_data_dir, migrate_legacy_data,
    resolve_base_dir, save_data_dir, migrate_data, location_file)
from WisePet.infra.frame_loader import FrameAssetLoader                # noqa: E402
from WisePet.infra.global_hotkey import GlobalHotKey                   # noqa: E402
from WisePet.infra.growth_store import GrowthStore                     # noqa: E402
from WisePet.infra.instance_guard import InstanceGuard                 # noqa: E402
from WisePet.infra.logger_setup import reconfigure_logging, setup_logging  # noqa: E402
from WisePet.infra.news_feed import fetch_headlines                    # noqa: E402
from WisePet.infra.platform_idle import idle_seconds                    # noqa: E402
from WisePet.infra.platform_util import set_notification_app_id         # noqa: E402
from WisePet.infra.reminder_store import ReminderStore                 # noqa: E402
from WisePet.infra.tts import create_synthesizer                       # noqa: E402
from WisePet.infra.weekly_store import WeeklyStore                     # noqa: E402
from WisePet.infra.openai_client import OpenAICompatClient             # noqa: E402
from WisePet.infra.ppt_builder import build_ppt                        # noqa: E402
from WisePet.infra.product_store import (                              # noqa: E402
    ActionAuditStore, MemoryStore, QuietStore)
from WisePet.infra.telemetry_store import TelemetryStore               # noqa: E402
from WisePet.infra.pet_state_repo import PetStateRepository            # noqa: E402
from WisePet.infra.event_store import EventStore                        # noqa: E402
from WisePet.infra.knowledge_store import KnowledgeStore                # noqa: E402
from WisePet.infra.presence_store import PresenceStore                  # noqa: E402
from WisePet.infra.profile_store import (                              # noqa: E402
    ProfileStore, guard_port_for)
from WisePet.infra.profile_store import safe_name as safe_profile_name  # noqa: E402
from WisePet.infra.task_store import TaskStore                          # noqa: E402
from WisePet.infra.system_clock import SystemClock                     # noqa: E402
from WisePet.infra.web_search import WebSearchGateway                   # noqa: E402
from WisePet.infra.geo_location import GeoLocator                       # noqa: E402
from WisePet.infra.weather_api import WeatherGateway                    # noqa: E402
from WisePet.app.weather_service import WeatherService                  # noqa: E402
from WisePet.infra.amap_api import AmapPoiGateway                       # noqa: E402
from WisePet.ui.action_confirm import ActionConfirmDialog               # noqa: E402
from WisePet.ui.input_dialog import ChoiceDialog                        # noqa: E402
from WisePet.ui.command_palette import CommandPalette                   # noqa: E402
from WisePet.ui.ai_worker import AiWorker                              # noqa: E402
from WisePet.ui.onboarding_dialog import OnboardingDialog               # noqa: E402
from WisePet.ui.profile_picker import ProfilePickerDialog               # noqa: E402
from WisePet.ui.chat_input import ChatInput                             # noqa: E402
from WisePet.ui.chat_text_edit import attachment_from_clipboard_image   # noqa: E402
from WisePet.ui.chat_window import ChatWindow                           # noqa: E402
from WisePet.ui.consent_bridge import LocationConsentBridge             # noqa: E402
from WisePet.ui.position_bridge import PositionBridge                   # noqa: E402
from WisePet.ui.frame_animator import CANVAS_SIZE, FrameAnimator       # noqa: E402
from WisePet.ui.pet_window import PetWindow                            # noqa: E402
from WisePet.ui.qt_bridge import EventRouter, QtEventBridge            # noqa: E402
from WisePet.ui.region_selector import RegionSelector                   # noqa: E402
from WisePet.ui.screen_bounds import ScreenBounds                     # noqa: E402
from WisePet.ui.settings_dialog import SettingsDialog                  # noqa: E402
from WisePet.ui.speech_bubble import SpeechBubble                      # noqa: E402
from WisePet.ui.memory_dialog import MemoryDialog                       # noqa: E402
from WisePet.ui.stats_dialog import StatsDialog                        # noqa: E402
from WisePet.ui.tech_effects import (ENTER_KINDS, TechEffect,         # noqa: E402
                                      TRIGGER_ENTER_KIND, random_enter_kind)
from WisePet.ui.tray_icon import HIDE_DISABLED_HINT, PetTrayIcon          # noqa: E402
from WisePet.ui.tray_menu import PetMenu                               # noqa: E402

logger = logging.getLogger("WisePet.main")

#: 主计时器周期：1s 足够驱动衰减/自主行为/提醒，且空闲 CPU 极低（PRD NFR）
TICK_INTERVAL_MS = 1000
#: 运动计时器周期：只在宠物真的在动时才跑（走动/小跑/下落），
#: 1s 粒度下 60px/s 的走动是每秒瞬移一次，观感上就不像在走（PRD v2.0 C10）
MOTION_INTERVAL_MS = 50
PET_ID = "mochi"
#: 宠物窗口边长与动画画布一致，便于把位置/锚点换算统一交给工作区 bounds
PET_SIZE = CANVAS_SIZE

#: 命令面板注册表（id, 标题, 说明）：把散落在右键菜单 / 设置分区 / 托盘里的
#: 能力收成一处可搜索入口，避免菜单继续膨胀。id 同时用作本地埋点事件名。
COMMANDS: tuple[tuple[str, str, str], ...] = (
    ("chat", "跟我聊天", "打开输入条"),
    ("history", "历史对话", "按天回看并继续聊"),
    ("search", "搜聊天记录", "跨日全文检索"),
    ("reminder", "排个提醒", "1 分钟后提醒喝水"),
    ("focus", "番茄钟", "开始一轮专注"),
    ("scene_healthy", "恢复热闹", "健康作息：重新打开推送"),
    ("scene_deep", "安静一会儿", "深度工作：少打扰"),
    ("why_quiet", "为什么没弹窗", "当前场景 / 额度 / 免打扰"),
    ("export", "导出聊天记录", "写到本机 Markdown"),
    ("usage", "本机数据占用", "记录数与磁盘大小"),
    ("locate_diag", "定位诊断", "当前定位来源 / 坐标 / 精度"),
    ("metrics", "陪伴指标", "激活 / 留存 / 打扰护栏"),
    ("clear_metrics", "清空本地埋点", "一键清除本机指标"),
    ("memory", "我记得你什么", "本地长期记忆时间线"),
    ("audit", "最近执行的操作", "写盘 / 工具历史"),
    ("settings", "打开设置", "系统配置等分区"),
    ("check_update", "检查更新", "看看有没有新版本"),
    ("install_update", "安装更新", "下载并打开更新包"),
    ("stats", "陪伴统计", "天数 / 消息数 / 等级"),
    ("call", "叫一声小墨", "打断它当下在做的事，让它抬头看你"),
    ("recall", "我还记得什么", "看看它惦记着哪几句旧话"),
    ("tasks", "我的托付", "列出正在替你盯着的事"),
    ("cancel_tasks", "取消全部托付", "不再盯、不再催"),
    ("knowledge_build", "重建资料索引", "重读你指定的本机文件夹"),
    ("knowledge_status", "资料索引状态", "读了哪些文件、多少片段"),
    ("profile_switch", "切换身份", "本机多用户（家人共机），重启生效"),
    ("family_share", "共享我的 AI 给全家", "家长配一次 Key，家人勾选即可共用"),
)
#: 启动问候兜底语（未连模型或生成失败时；{name} 填宠物名）
STARTUP_GREETING_FALLBACK = "{name}来召唤你啦，让我们开启美好的一天吧！"
#: 开机问候的 AI 等待上限（秒）：远小于聊天 timeout_s，超时即回落兜底语，不拖慢开机问候
GREETING_TIMEOUT_S = 6.0
#: 开机元气基线：任一 vital 低于此值启动时一律托底，保证「一睁眼就精神」不触发求助
FRESH_START_VITALS = 60.0

#: 进程存活期必须持有单实例守卫，否则命名管道/端口会被回收释放
_instance_guard: InstanceGuard | None = None


class PetApplication:
    """把装配、事件订阅、定时器与生命周期收在一处，便于整体冒烟测试。"""

    def __init__(self, app: QApplication,
                 config_repo: JsonConfigRepository | None = None,
                 state_repo: PetStateRepository | None = None,
                 assets_dir: Path | str | None = None,
                 clock: SystemClock | None = None,
                 chat_log_repo: SqliteChatRepository | None = None,
                 tray_available: bool | None = None,
                 onboarding: bool = False,
                 profile: str = "",
                 shared_ai: SharedAIStore | None = None,
                 base_dir: Path | str | None = None):
        self._app = app
        # 数据根目录（自定义位置时可能不是默认 ~/.WisePet）；身份/profile 都在其下。
        # 未显式传入时从 config_repo 推导：默认身份的 data_dir 就是 base_dir，
        # 这样注入 tmp config_repo 的测试也能得到正确根，不碰真实 home。
        if base_dir:
            self._base_dir = Path(base_dir)
        elif config_repo is not None:
            self._base_dir = Path(config_repo.path.parent)
        else:
            self._base_dir = resolve_base_dir()
        # 当前身份（v1.31 本地多用户）：空串 = 默认身份（管理员）
        self._profile = str(profile or "")
        # 默认 False：offscreen 组装测试不弹引导；只有真实入口 main() 判定首次运行才打开
        self._onboarding_enabled = onboarding
        # 宠物藏起来后进程没有任何可见窗口；不关掉这个开关，事件循环会跟着退出
        self._app.setQuitOnLastWindowClosed(False)
        self._config_repo = config_repo or JsonConfigRepository()
        # 当前身份的数据目录（默认身份=根，命名身份=profiles/<名>），所有仓储落这里
        self._data_dir = self._config_repo.path.parent
        self._state_repo = state_repo or PetStateRepository(self._data_dir)
        self._clock = clock or SystemClock()
        self._assets_dir = Path(assets_dir) if assets_dir else ROOT / "assets"
        self._config = self._config_repo.load()
        # 自动更新：当前版本 + 更新网关（repo/开关从 system 配置惰性读）
        import WisePet
        self._current_version = read_current_version(ROOT, WisePet.__version__)
        self._updater = UpdateService(
            lambda: self._config.system.update_repo, self._current_version)
        self._update_available = None    # 后台检查结果（ReleaseInfo | None）
        self._nudge_day = ""             # 指标主动关怀当日已检查（day_key 去重）
        # 聊天记录改用同目录下的 SQLite 单文件（其余持久化文件仍是 JSON）；
        # 构造时会把旧 chat/*.json 一次性导入并归档，测试注入 tmp_path 不污染真实目录
        self._chat_log_repo = chat_log_repo or SqliteChatRepository(
            self._config_repo.path.parent)
        self._chat_history = ChatHistory(self._chat_log_repo)
        # P1/P2 本地数据：打扰预算计数与长期事实记忆，和配置/聊天记录同目录
        self._quiet_budget = QuietBudget(QuietStore(self._data_dir), self._clock,
                                         lambda: self._config)
        self._memory_store = MemoryStore(self._data_dir)
        self._memory = LongTermMemory(
            self._memory_store, lambda: self._config.memory.enabled,
            clock=self._clock,
            ttl_provider=lambda: self._config.memory.fact_ttl_days)
        # P0：本地埋点（只记事件名与按天计数，绝不上传）+ 崩溃本地留痕
        self._telemetry = TelemetryService(TelemetryStore(self._data_dir), self._clock)
        self._telemetry.install_crash_hook()
        self._audit_store = ActionAuditStore(self._data_dir)
        self._audit = ActionAudit(self._audit_store)
        self._data_steward = DataSteward(self._chat_log_repo, self._data_dir,
                                         telemetry=self._telemetry)
        self._active_scene = "自定义"
        self._last_action_receipt: dict | None = None

        self._reminder_kind: str | None = None
        self._streaming = False
        self._last_save_at = self._clock.monotonic()
        # 输入条 / 历史窗各自可开可关，任一开着都算「会话中」（宠物原地不动）
        self._chat_ui_active: dict[str, bool] = {"input": False, "history": False}

        self._bus = EventBus()
        self._bridge = QtEventBridge(self._bus, parent=self._app)
        self._router = EventRouter(self._bridge)
        self._screen = ScreenBounds()

        self._pet = self._restore_pet()
        self._controller = PetController(
            pet=self._pet, bus=self._bus, clock=self._clock,
            config_provider=lambda: self._config,
            bounds_provider=lambda: self._screen.bounds_for(PET_SIZE, PET_SIZE),
            context_provider=self._behavior_context)
        self._session = ChatSession(max_turns=self._config.pet.max_turns)
        self._local = LocalResponder(context_provider=self._local_context,
                                     on_remind=self._schedule_remind_action)
        self._ai_client = OpenAICompatClient()
        # 家庭共享 AI（全局）：默认真实用户目录；测试注入 tmp 隔离，
        # 否则开发机上真实 shared_ai.json 会泄漏进测试（真机踩过）
        self._shared_ai = shared_ai or SharedAIStore(self._base_dir)
        self._ai_usage = AIUsageStore(self._data_dir)         # 本身份每日用量
        self._chat = ChatService(
            gateway=self._ai_client, session=self._session, bus=self._bus,
            config_provider=lambda: self._effective_ai(),
            system_prompt_provider=self._system_prompt,
            local_responder=self._local,
            tool_runner=self._run_agent,
            summary_observer=self._observe_summary,
            summary_prefix_provider=lambda: self._summary.prefix_line(),
            ai_allowed_provider=lambda: self._ai_within_quota(),
            on_ai_request=self._count_ai_request)
        self._reminder = ReminderService(
            clock=self._clock, bus=self._bus,
            config_provider=lambda: self._config,
            deferral_provider=self._quiet_deferral)
        self._reminder.start()
        self._reminder_store = ReminderStore(self._config_repo.path.parent)
        self._one_shot = OneShotReminders(
            clock=self._clock, bus=self._bus, polisher=self._polish_reminder_text,
            load_entries=self._reminder_store.load_entries,
            save_entries=self._reminder_store.save_entries)
        self._news = NewsService(
            clock=self._clock, bus=self._bus, config_provider=lambda: self._config,
            fetcher=fetch_headlines, summarizer=self._summarize_news,
            deferral_provider=self._quiet_deferral)
        self._cheer = CheerService(clock=self._clock, bus=self._bus,
                                   config_provider=lambda: self._config,
                                   generator=self._generate_cheer,
                                   deferral_provider=self._quiet_deferral)
        self._focus = FocusService(
            clock=self._clock, bus=self._bus,
            append_entry=self._append_focus_entry)
        # v2.2 陪伴进阶：成长体系 / 每周回忆录 / 本地语音（TTS）
        self._growth = GrowthService(
            clock=self._clock, bus=self._bus,
            store=GrowthStore(self._config_repo.path.parent),
            config_provider=lambda: self._config)
        self._weekly = WeeklyReportService(
            clock=self._clock, bus=self._bus, config_provider=lambda: self._config,
            records_provider=lambda: self._chat_history.recent_turns(7),
            store=WeeklyStore(self._config_repo.path.parent),
            summarizer=self._summarize_week, deferral_provider=self._quiet_deferral)
        self._tts = TtsAnnouncer(create_synthesizer(enabled=True),
                                 lambda: self._config)
        # 陪伴轴：精确「上次出现」时间戳 + 惦记 / 久别 / 情境（独立于按天刷新的 growth/telemetry）
        self._presence = PresenceStore(self._data_dir)
        self._companion = CompanionService(
            clock=self._clock, bus=self._bus,
            config_provider=lambda: self._config,
            records_provider=lambda: self._chat_history.recent_turns(21),
            store=self._presence,
            name_provider=lambda: self._pet.name)
        # 近期处境：只认用户明说的客观近况（加班/备考/生病…），过期自然消退
        self._situation = SituationService(
            clock=self._clock, bus=self._bus,
            store=SituationStore(self._data_dir),
            config_provider=lambda: self._config,
            name_provider=lambda: self._pet.name)
        # 轴②助理能力：可托付任务 / 本地资料索引 / 事件驱动主动（全本机）
        self._task_store = TaskStore(self._data_dir)
        self._tasks = TaskService(
            clock=self._clock, bus=self._bus,
            config_provider=lambda: self._config, store=self._task_store,
            audit=self._audit,
            enabled_provider=lambda: (self._config.assistant.enabled
                                      and self._config.assistant.tasks_enabled),
            page_fetcher=self._fetch_page_text)
        self._knowledge_store = KnowledgeStore(self._data_dir)
        self._knowledge = KnowledgeBase(
            self._knowledge_store,
            embedding_fn=self._embed_texts)
        self._summary_store = SummaryStore(self._data_dir)
        self._summary = SummaryMemory(
            self._summary_store, self._ai_client, lambda: self._effective_ai(),
            enabled_provider=lambda: (self._config.memory.enabled
                                      and self._config.memory.summary_enabled
                                      and self._effective_ai().configured))
        # 统一记忆检索：一个问题查遍事实/聊天/资料/近况/摘要，供 agent 的 recall 工具
        from WisePet.app.unified_memory import UnifiedMemory
        self._unified_memory = UnifiedMemory(
            long_term=self._memory, chat_history=self._chat_history,
            knowledge=self._knowledge, situation=self._situation,
            summary=self._summary)
        # 通用事实记忆：AI 自己判断什么值得记（名字/喜好/约定/项目…），不靠正则
        self._auto_facts = AutoFactExtractor(
            self._memory, self._ai_client, lambda: self._effective_ai(),
            enabled_provider=lambda: (self._config.memory.enabled
                                      and self._effective_ai().configured),
            situation_sink=self._situation.learn_from_ai)
        self._event_store = EventStore(self._data_dir)
        self._events = EventTriggerService(
            clock=self._clock, bus=self._bus,
            config_provider=lambda: self._config, store=self._event_store,
            idle_provider=idle_seconds,
            focus_count_provider=lambda: FocusLog.summarize(
                self._load_focus_entries(), day_key(self._clock.now()))[1],
            dismiss_rate_provider=lambda: self._telemetry.metrics().get(
                "reminder_dismiss_rate"),
            weather_line_provider=lambda now, day_key_fn: (
                self._weather.due_line(now, day_key_fn=day_key_fn)
                if hasattr(self, "_weather") else ""))
        self._timeline = MemoryTimeline(
            memory_store=self._memory_store, memory_service=self._memory,
            audit_store=self._audit_store, chat_repo=self._chat_log_repo,
            telemetry=self._telemetry, presence_store=self._presence,
            task_service=self._tasks, knowledge=self._knowledge,
            event_store=self._event_store)
        self._quota_warned_day = ""
        # 轴②：agent 工具循环（function calling 多步）——写盘动作交回主线程确认
        # 联网能力：web_search（免 Key DuckDuckGo / 可选 Tavily・博查）与
        # IP 城市级定位；位置授权弹窗桥在 worker 线程阻塞等主线程弹窗结果
        self._web_search = WebSearchGateway(
            provider_provider=lambda: self._config.web_search,
            key_provider=lambda: self._config.web_search.api_key)
        self._position_bridge = PositionBridge()   # 系统级精确定位（主线程异步）
        self._geo = GeoLocator(
            precise_provider=(self._position_bridge.request
                              if self._position_bridge.available else None))
        # 可选天气推送：默认关闭，开启后后台抓 Open-Meteo（免 Key）
        self._weather_gateway = WeatherGateway(geo=self._geo)
        self._weather = WeatherService(
            self._weather_gateway, lambda: self._config,
            now_provider=self._clock.now)
        self._amap = AmapPoiGateway(
            key_provider=lambda: self._config.web_search.amap_key)
        self._consent_bridge = LocationConsentBridge(
            self._ask_location_consent, parent=None)
        self._toolbox = AgentToolbox(
            knowledge=self._knowledge, chat_history=self._chat_history,
            memory_service=self._memory, tasks=self._tasks, pet=self._pet,
            config_provider=lambda: self._config,
            remind_callback=self._schedule_remind_action,
            web_search=self._web_search, geo=self._geo, amap=self._amap,
            weather=self._weather_gateway,
            location_consent=self._consent_bridge.ask,
            image_gateway=self._ai_client,
            image_dir=Path(self._data_dir) / "generated",
            image_cfg_provider=self._image_ai,
            video_gateway=self._ai_client,
            video_dir=Path(self._data_dir) / "generated",
            video_cfg_provider=self._video_ai,
            video_progress=self._on_video_progress,
            video_task=self._on_video_task,
            unified_memory=self._unified_memory)
        self._agent = AgentRunner(
            self._ai_client, lambda: self._effective_ai(),
            lambda: self._system_prompt(), self._toolbox,
            keyword_fallbacks=((wants_image, "generate_image"),
                               (wants_video, "generate_video"),))
        self._pending_query = ""

        self._animator = FrameAnimator(FrameAssetLoader(self._assets_dir,
                                                        self._config.pet.skin),
                                       size=PET_SIZE, parent=self._app)
        self._window = PetWindow(self._controller, self._animator,
                                 lambda: self._config, self._screen, size=PET_SIZE,
                                 growth_provider=self._growth_decoration)
        self._bubble = SpeechBubble(
            self._screen, duration_provider=lambda: self._config.bubble.duration_s,
            avoid_provider=lambda: self._window.window_rect())
        self._chat_input = ChatInput(self._screen,                # 点宠物：正常聊天（单行输入条）
                                     idle_hide_provider=lambda: self._config.chat.idle_hide_s)
        self._chat_window = ChatWindow(                # 右键：可继续聊的历史窗
            self._screen,
            days_provider=self._chat_history.days,
            records_provider=self._chat_history.records_for,
            search_provider=self._search_chat)
        self._menu = PetMenu(on_settings=self.open_settings,
                             on_feed=self._on_feed,
                             on_pet_head=self._on_pet_head,
                             on_toggle_visible=self.toggle_visible,
                             on_quit=self._exit_app,
                             on_reload=self.reload_assets,
                             on_history=self._show_history,
                             on_focus=lambda: self._start_focus(
                                 self._config.focus.default_min),
                             on_stats=self._show_stats,
                             on_screenshot=self._screenshot_ask,
                             on_scene=self._apply_scene,
                             on_toggle_mute=self._toggle_mute,
                             on_undo=self._undo_last_action,
                             on_onboarding=self._show_onboarding,
                             on_palette=self._show_palette,
                             on_profile=self._switch_profile)
        self._stats_dialog = StatsDialog(self._screen)
        self._memory_dialog = MemoryDialog(
            self._timeline, export_path=self._data_dir / "memory-timeline.md",
            screen_bounds=self._screen)
        self._onboarding_dialog = OnboardingDialog()
        self._onboarding_dialog.try_chat.connect(self._show_chat_input)
        self._onboarding_dialog.try_reminder.connect(self._demo_reminder)
        self._onboarding_dialog.completed.connect(self._mark_onboarding_done)
        self._command_palette = CommandPalette(list(COMMANDS))
        self._command_palette.executed.connect(self._run_command)
        # 无边框 Tool 窗隐藏后没有可右键之处，托盘是唯一还能点的入口（FR v1.11 C21）
        self._tray = PetTrayIcon(self._menu, self._animator.current_frame(),
                                 on_activate=self.toggle_visible, parent=self._app)
        self._tray_available = (PetTrayIcon.is_available()
                                if tray_available is None else tray_available)
        if not self._tray_available:
            logger.warning("system tray unavailable: hide action disabled "
                           "to avoid losing the pet")
            self._menu.act_toggle.setEnabled(False)
            self._menu.act_toggle.setToolTip(HIDE_DISABLED_HINT)
        self._worker = AiWorker(self._chat, parent=self._app)
        self._menu.set_muted(self._config.quiet.muted)
        self._menu.set_undo_enabled(self._last_action_receipt is not None)
        self._refresh_companion_status()
        self._memory.hydrate(getattr(self._local, "_engine", None))

        self._tick_timer = None          # start() 后才挂，测试可只构建不启动
        self._motion_timer = None        # 同上：只在需要位移推进时短时开启
        self._wire_ui()
        self._wire_events()

    # ---------- P1/P2：引导 / 场景包 / 因果驱动 / 打扰预算 / 长期记忆 ----------

    def _behavior_context(self) -> BehaviorContext | None:
        """给控制器注入「此刻的生活情境」；关掉因果驱动时返回 None 走原轮播。"""
        if not self._config.behavior.context_driven:
            return None
        local = time.localtime(self._clock.now())
        contextual = (self._config.companion.enabled
                      and self._config.companion.contextual)
        return BehaviorContext(
            hour=local.tm_hour,
            focusing=bool(getattr(self._focus, "running", False)),
            quiet=self._config.quiet.muted or self._quiet_deferral() > 0,
            idle_s=float(idle_seconds() or 0.0),
            # 关掉「关系感情境」时传 -1，行为与旧版逐字节一致
            weekday=local.tm_wday if contextual else -1,
            just_finished_focus=bool(contextual and self._focus.just_finished()))

    def _companion_phrase(self) -> str:
        """话术侧的情境短语（周一早晨 / 深夜 / 刚结束一场专注）。"""
        cfg = self._config.companion
        if not cfg.enabled or not cfg.contextual:
            return ""
        local = time.localtime(self._clock.now())
        return context_phrase(
            hour=local.tm_hour, weekday=local.tm_wday,
            focusing=bool(getattr(self._focus, "running", False)),
            quiet=self._config.quiet.muted or self._quiet_deferral() > 0,
            just_finished_focus=self._focus.just_finished())

    def _growth_decoration(self):
        """等级 → 可见外观；关掉成长体系时不叠加任何装饰。"""
        if not self._config.growth.enabled:
            return growth_decoration(1, enabled=False)
        return growth_decoration(self._growth.current().level)

    def _search_chat(self, query: str) -> list:
        if not self._config.memory.enabled:
            return []
        return self._chat_history.search(query, self._config.memory.search_max_results)

    def _remember_user_facts(self, text: str) -> None:
        """用户每说一句都尝试抽取长期事实；有新增才回填本地小脑与落盘。"""
        if self._memory.learn_from(text):
            self._memory.hydrate(getattr(self._local, "_engine", None))

    def _apply_scene(self, scene_id: str) -> None:
        updated = apply_scene(self._config, scene_id)
        if updated is self._config:
            return
        self.apply_config(updated)
        self._active_scene = scene_label(scene_id)
        self._refresh_companion_status()
        # 场景包会一次改掉多个开关，只说「换好了」用户根本不知道弹窗为什么消失。
        quiet = updated.quiet
        notice = (f"已切到「{scene_label(scene_id)}」："
                  f"新闻 {'开' if updated.news.enabled else '关'}、"
                  f"鼓励 {'开' if updated.cheer.enabled else '关'}、"
                  f"周报 {'开' if updated.weekly.enabled else '关'}；"
                  f"主动推送每天 {quiet.daily_limit} 条（同类 {quiet.per_source_limit} 条）。")
        if not (updated.news.enabled or updated.cheer.enabled or updated.weekly.enabled):
            notice += " 定时推送都关了，弹窗会明显变少；想恢复选「健康作息」。"
        self._controller.speak(notice, source="system")

    def _toggle_mute(self) -> None:
        muted = not self._config.quiet.muted
        self.apply_config(replace(self._config,
                                  quiet=replace(self._config.quiet, muted=muted)))
        self._controller.speak("免打扰已经打开，我先安静一会儿。" if muted
                               else "我回来啦，有提醒还会叫你。", source="system")

    def _onboarding_done(self) -> bool:
        return (self._data_dir / "onboarding.done").exists()

    def _mark_onboarding_done(self) -> None:
        try:
            (self._data_dir / "onboarding.done").write_text("1", encoding="utf-8")
        except OSError:
            logger.warning("onboarding marker write failed")

    def _show_onboarding(self) -> None:
        self._onboarding_dialog.show_centered()

    def _demo_reminder(self) -> None:
        """引导里的「现场兑现」：真的排一条 1 分钟后的提醒，不是空口说明。"""
        self._one_shot.schedule(RemindAction(text="喝口水，顺便伸个懒腰吧",
                                             delay_min=1.0))
        self._controller.speak("好，1 分钟后我来叫你～", source="system")

    # ---------- P0/P1/P2：命令面板 · 陪伴状态 · 本地数据管家 ----------

    def _report(self, text: str) -> None:
        """命令结果用自绘气泡反馈；不经过打扰预算，保证用户主动发起必达。"""
        self._controller.start_speaking()
        self._bubble.show_text(
            text, source="system", title="WisePet",
            anchor=self._window.bubble_anchor(),
            on_close=self._controller.on_action_finished)
        self._sync_window()

    def _companion_status_text(self) -> str:
        cfg = self._config
        used = self._quiet_budget.usage()
        return (f"当前：{self._active_scene} ｜ "
                f"新闻{'开' if cfg.news.enabled else '关'} "
                f"鼓励{'开' if cfg.cheer.enabled else '关'} "
                f"周报{'开' if cfg.weekly.enabled else '关'} ｜ "
                f"今日推送 {used['total']}/{cfg.quiet.daily_limit} ｜ "
                f"免打扰{'开' if cfg.quiet.muted else '关'}")

    def _refresh_companion_status(self) -> None:
        self._menu.set_companion_status(self._companion_status_text())

    def _show_palette(self) -> None:
        self._command_palette.show_centered()

    def _run_agent(self, text: str, messages: list[dict],
                   on_content=None, reasoning_callback=None):
        """ChatService 的 agent 入口：开关关闭时返回 None 走原纯文本流式。

        on_content 为真流式增量回调（模型边生成边投递）；None 走一次性返回。
        reasoning_callback 接推理模型的思考过程增量。
        """
        cfg = self._config.assistant
        if not cfg.enabled or not cfg.tool_calling_enabled:
            return None
        # 工具对所有模型开放（v1.35 修正：曾按 lite 档禁用，误伤 glm-4-flash
        # 的 get_location/nearby_pois，导致「我查不到」）。防编造靠提示词与
        # 工具结果注入，不靠剥夺工具。
        outcome = self._agent.run(text, messages, content_callback=on_content,
                                  reasoning_callback=reasoning_callback)
        if outcome is not None:
            for _ in outcome.tools_used:
                self._telemetry.track("tool:used")
        return outcome

    def _effective_ai(self) -> AIConfig:
        """生效 AI 配置：成员跟随家长共享，家长自己用本地（共享源就是他）。

        家长身份（勾了「共享给全家人」）配置的是共享的源头，若这里再读
        shared_ai.json 回灌自己，就会用旧快照盖掉刚切好的方案——真机出现过
        「本地是 agnes 的 URL、实际却打去 bigmodel，还用着旧 Key」。
        成员跟随共享时：Key 与端点用家长的（这是共享的本意），model 用自己的
        方案选的；没配共享则退回本地配置。
        """
        cfg = self._config
        if cfg.assistant.share_family_ai:
            return cfg.ai                      # 共享源：永远用自己最新的配置
        if cfg.assistant.use_family_ai:
            shared = self._shared_ai.load()
            if shared is not None:
                return replace(cfg.ai, base_url=shared.base_url,
                               api_key=shared.api_key)
            logger.warning("family shared AI requested but not configured")
        return cfg.ai

    def _image_ai(self) -> AIConfig | None:
        """取「用途=image」的模型方案；没有就返回 None（画图工具会提示去配置）。"""
        for profile in self._config.ai_profiles:
            if normalize_purpose(getattr(profile, "purpose", "")) != PURPOSE_IMAGE:
                continue
            if not str(profile.api_key or "").strip():
                continue
            return replace(self._config.ai, base_url=profile.base_url,
                           api_key=profile.api_key, model=profile.model,
                           purpose=PURPOSE_IMAGE)
        return None

    def _video_ai(self) -> AIConfig | None:
        """取「用途=video」的模型方案；没有就返回 None（视频工具会提示去配置）。"""
        for profile in self._config.ai_profiles:
            if normalize_purpose(getattr(profile, "purpose", "")) != PURPOSE_VIDEO:
                continue
            if not str(profile.api_key or "").strip():
                continue
            return replace(self._config.ai, base_url=profile.base_url,
                           api_key=profile.api_key, model=profile.model,
                           purpose=PURPOSE_VIDEO)
        return None

    def _on_video_progress(self, text: str) -> None:
        """视频生成的异步进度 → 独立任务卡（进度不塞进普通会话状态，避免被覆盖）。"""
        self._bus.publish(MediaTaskEvent(task_id="current", status="progress",
                                         text=text))

    def _on_video_task(self, task_id: str, status: str, text: str, path: str) -> None:
        """视频任务提交/完成/失败 → 独立任务卡事件。"""
        self._bus.publish(MediaTaskEvent(task_id=task_id, status=status,
                                         text=text, path=path))

    def _show_task_note(self, text: str, *, videos: tuple[str, ...] = ()) -> None:
        """任务提示放进历史窗（用户正盯着它看），同时弹桌面气泡兜底。"""
        from WisePet.domain.chat_record import ChatRecord
        note = ChatRecord(role="assistant", content=text, source="system",
                          ts=time.time(), session_id=day_key(), videos=videos)
        self._chat_window.receive_record(note)
        self._controller.speak(text, source="system")

    def _on_media_task(self, event: MediaTaskEvent) -> None:
        """异步媒体任务：关键节点在历史窗+气泡都提醒，进度只更状态栏。"""
        if event.status == "progress":
            self._set_chat_state(PHASE_THINKING, detail=event.text)
            return
        if event.status == "submitted":
            self._show_task_note("🎬 视频任务已提交，生成中可能要几分钟，"
                                 "做好了我会在这里告诉你～")
            return
        if event.status == "done":
            name = Path(event.path).name if event.path else "（未找到文件）"
            folder = str(Path(event.path).parent) if event.path else ""
            self._show_task_note(
                f"🎬 视频做好啦！文件名 {name}，存在 {folder}。"
                "点下方「播放」或「保存视频」就能用。",
                videos=(event.path,) if event.path else ())
            return
        if event.status == "failed":
            self._show_task_note(f"🎬 视频没做成：{event.text}")

    def _ai_within_quota(self) -> bool:
        """每日 AI 请求配额（防孩子过度消费）；超限回落本地小脑并提示一次。"""
        limit = int(self._config.assistant.daily_ai_limit)
        if limit <= 0:
            return True
        today = day_key(self._clock.now())
        if self._ai_usage.today(today) < limit:
            return True
        if self._quota_warned_day != today:
            self._quota_warned_day = today
            self._controller.speak(
                f"今天的 AI 次数用完啦（{limit} 次/天），我先陪你本地聊～明天恢复。",
                source="pet")
        return False

    def _count_ai_request(self) -> None:
        self._ai_usage.increment(day_key(self._clock.now()))
        self._telemetry.track("model:request")

    def _fetch_page_text(self, url: str) -> str:
        """网页盯梢的抓取：复用合并信任链客户端，只取纯文本前若干字。"""
        from WisePet.infra.web_search import _HttpClient
        import re as _re
        resp = _HttpClient.get().get(url, timeout=12.0,
                                     headers={"Accept-Language": "zh-CN,zh;q=0.9"})
        resp.raise_for_status()
        text = _re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ",
                       resp.text, flags=_re.I)
        text = _re.sub(r"<[^>]+>", " ", text)
        return _re.sub(r"\s+", " ", text).strip()[:20000]

    def _maybe_nudge(self, now: float) -> None:
        """指标会说话：每天一次，沉默够久时宠物主动问一句（不过度打扰）。"""
        today = day_key(now)
        if self._nudge_day == today:
            return
        self._nudge_day = today
        try:
            metrics = self._telemetry.metrics()
            days_since = metrics.get("days_since_last_engagement")
        except Exception:  # noqa: BLE001
            return
        if should_nudge(days_since):
            # 情感陪伴：心情低且沉默够久时，用「闹小脾气」话术替代普通问候
            idle_hours = float(days_since) * 24.0
            tone = sulk_line(idle_hours, self._pet.vitals.mood)
            text = tone or nudge_line(int(days_since), self._config.persona.name)
            self._bus.publish(SpeakRequestedEvent(
                text, source="system", title="我在呢"))   # 标题在 PUSH_TITLES，走 QuietBudget
            logger.info("care nudge fired: days_since=%s sulk=%s",
                        days_since, bool(tone))

    # ---------- 自动更新 ----------

    def _check_update_background(self, repo: str = "") -> None:
        """后台线程检查更新；结果排队回主线程。repo 非空时用传入值（手动检查）。"""
        if repo and "/" in repo:
            from WisePet.infra.update_service import UpdateService
            updater = UpdateService(lambda: repo, self._current_version)
        else:
            if not (self._config.system.auto_check_update
                    and self._config.system.update_repo.strip()):
                if not repo:
                    return
            updater = self._updater

        def work():
            info = updater.check()
            self._bus.publish(UpdateCheckResultEvent(info=info, manual=bool(repo)))

        threading.Thread(target=work, daemon=True, name="wisePet-update-check").start()

    def _on_update_result(self, event) -> None:
        """主线程：处理检查结果。"""
        self._update_available = event.info
        if event.info is None:
            if event.manual:
                self._report(f"已是最新版本（{self._current_version}），或暂时无法连接更新源。")
            return
        self._report(f"发现新版本 {event.info.version}（当前 {self._current_version}）。"
                     "命令面板执行「安装更新」下载，或到设置里查看。")
        logger.info("update available: %s", event.info.version)

    def _install_update(self) -> None:
        """后台下载 + 主线程打开安装包（不静默自替换运行中的程序）。"""
        info = self._update_available
        if info is None or not info.download_url:
            self._report("当前没有可安装的更新，先检查更新试试。")
            return
        self._report("正在下载更新包，请稍候…")

        def work():
            path = self._updater.download(info, ROOT / "tmp" / "updates")
            self._bus.publish(UpdateDownloadedEvent(path=path))

        threading.Thread(target=work, daemon=True, name="wisePet-update-dl").start()

    def _on_update_downloaded(self, event) -> None:
        """主线程：下载完成，打开安装包/所在目录交给系统安装。"""
        if not event.path:
            self._report("更新包下载失败，请稍后重试或到 Release 页手动下载。")
            return
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(event.path))
            self._report("已打开更新包，按提示完成安装后重启 WisePet 即可。")
            logger.info("update installer opened: %s", event.path)
        except Exception:  # noqa: BLE001
            self._report(f"更新包已下载到 {event.path}，请手动打开安装。")

    def _run_command(self, command_id: str) -> None:
        self._telemetry.track(f"cmd:{command_id}")
        if command_id == "chat":
            # 隐藏则登场（终端召唤+淡入）再开输入条；已可见则直接播终端特效层+开输入
            if not self._window.isVisible():
                self._enter_pet("chat", after=self._show_chat_input)
            else:
                self._play_effect_only("chat")
                self._show_chat_input()
        elif command_id == "history":
            self._show_history()
        elif command_id == "search":
            self._show_history()
            self._chat_window.search_input.setFocus()
        elif command_id == "reminder":
            self._demo_reminder()
            self._play_or_enter("celebrate")
        elif command_id == "focus":
            self._start_focus(self._config.focus.default_min)
            self._play_or_enter("celebrate")
        elif command_id in ("scene_healthy", "scene_deep"):
            self._apply_scene("healthy" if command_id == "scene_healthy" else "deep_work")
            self._play_or_enter("scene")
        elif command_id == "why_quiet":
            self._report(self._companion_status_text())
        elif command_id == "export":
            target = self._data_dir / "chat-export.md"
            count = self._data_steward.export_all(target)
            self._report(f"已导出 {count} 条聊天记录到 {target}")
        elif command_id == "usage":
            self._report(f"本机数据：{self._data_steward.usage()}")
        elif command_id == "check_update":
            repo = self._config.system.update_repo.strip()
            if not repo:
                self._report("还没配置更新源。设置 → 系统配置 → 更新源填 owner/repo（如 yourname/WisePet）。")
            else:
                self._report("正在检查更新…")
                self._check_update_background(repo)
        elif command_id == "install_update":
            self._install_update()
        elif command_id == "locate_diag":
            self._geo.locate()   # 触发一次定位以刷新诊断（命中缓存则零网络）
            key_ok = bool(str(self._config.web_search.amap_key or "").strip())
            sys_source = ("可用" if self._position_bridge.available else "不可用")
            self._report(
                "定位诊断：" + self._geo.last_diagnosis()
                + f"\n系统精确定位源：{sys_source}（不准确时是它超时回落了 IP）"
                + f"\n高德 Key：{'已配置' if key_ok else '未配置（附近搜索走网页降级）'}"
                + "\n想更准：① Windows 设置→隐私→位置服务打开；"
                "② 说的「附近」带区域名（如「桂林路 咖啡」），按区域名精确定位")
        elif command_id == "metrics":
            from WisePet.domain.metrics import GUARDRAIL_LABELS
            metrics = self._telemetry.metrics()
            lights = metrics.get("guardrails", {})
            light_text = " ｜ ".join(
                f"{label}={lights.get(key, 'n/a')}" for key, label in GUARDRAIL_LABELS)
            self._report(f"有效互动 {metrics['engaged_days']} 天 ｜ "
                         f"活跃 {metrics['active_days']} 天 ｜ "
                         f"提醒关闭率 {metrics['reminder_dismiss_rate']} ｜ "
                         f"崩溃 {metrics['crash_count']} 次 ｜ "
                         f"事件 {metrics['events_total']} 条\n护栏：" + light_text)
        elif command_id == "clear_metrics":
            self._telemetry.clear()
            self._report("本地埋点已清空（聊天记录与其它数据未动）。")
        elif command_id == "memory":
            # 一页看全本机数据并可勾选删除，而不是只报一句文本
            self._memory_dialog.reload()
            self._memory_dialog.show_centered()
        elif command_id == "audit":
            recent = self._audit.recent()
            self._report("最近操作：" + "；".join(
                f"{e['kind']}→{e['target']}（{e['result']}）" for e in recent)
                if recent else "还没有执行过写盘操作。")
        elif command_id == "settings":
            self.open_settings()
        elif command_id == "stats":
            self._show_stats()
        elif command_id == "call":
            self._call_pet()
        elif command_id == "recall":
            candidates = self._companion.recall_candidates()
            phrase = self._companion_phrase()
            prefix = f"现在是{phrase}，" if phrase else ""
            self._report(prefix + (
                "我还惦记着：" + "；".join(
                    f"{c.days_ago} 天前「{c.snippet}」" for c in candidates)
                if candidates else "还没有记下值得惦记的事～聊聊天就有了。"))
        elif command_id == "tasks":
            self._report("我的托付：\n" + self._tasks.describe())
        elif command_id == "cancel_tasks":
            count = self._tasks.cancel_all()
            self._report(f"好，取消了 {count} 个托付，我不再盯着了。" if count
                         else "现在没有正在进行的托付。")
        elif command_id == "knowledge_build":
            self._rebuild_knowledge()
        elif command_id == "knowledge_status":
            status = self._knowledge.status()
            self._report(f"资料索引：{status['files']} 个文件 / "
                         f"{status['chunks']} 个片段｜目录 {status['folder'] or '未设置'}"
                         f"｜开关 {'开' if self._config.assistant.knowledge_enabled else '关'}"
                         f"｜语义 {'开' if status.get('semantic') else '关'}"
                         f"（{status.get('vectors', 0)} 个向量）")
        elif command_id == "profile_switch":
            self._switch_profile()
        elif command_id == "family_share":
            if not self._is_manager():
                self._report("共享 AI 只有管理员（默认身份）能操作；"
                             "家人在设置里勾「使用家庭共享 AI」即可共用。")
                return
            if not self._config.ai.configured:
                self._report("先在设置里配好 AI（Key + 模型），再共享给全家。")
                return
            self._shared_ai.save(self._effective_ai())
            self._report("已把当前 AI 配置共享给全家——家人的设置里勾选"
                         "「使用家庭共享 AI」即可共用；Key 存在本机。")

    def _switch_profile(self) -> None:
        """本机多用户：弹出「谁来了？」选择（带 PIN 验证），选中即无缝换宠。

        与启动时的入口共用同一个对话框——验证逻辑只写一处。切换实现：
        保存当前状态 → 拉起新进程（新身份，独立守卫端口）→ 当前退场退出，
        用户看到的是宠物退场→新宠物进场，不需要手动重启。
        """
        profiles = ProfileStore(self._base_dir)
        picker = ProfilePickerDialog(profiles,
                                     current=profiles.active(),
                                     parent=self._window)
        if picker.exec() != QDialog.DialogCode.Accepted or picker.selected is None:
            return
        if picker.selected == profiles.active():
            self._report("现在就是这个身份啦～")
            return
        profiles.set_active(picker.selected) if picker.selected \
            else profiles.clear_active()
        self._relaunch_as(picker.selected)

    def _relaunch_as(self, profile: str) -> None:
        """保存状态 → startDetached 拉起新身份进程 → 当前退场退出。"""
        self.save_state()
        self._controller.speak("好，马上换人来陪它玩，我先把状态收好～",
                               source="pet")
        program = sys.executable
        args = [] if getattr(sys, "frozen", False) \
            else [str(Path(sys.argv[0]).resolve())]
        if profile:
            args.append(f"--profile={profile}")
        QProcess.startDetached(program, args, str(ROOT))
        logger.info("relaunching as profile=%s", profile or "默认")
        QTimer.singleShot(1200, self._exit_app)   # 让话先说一句再退场

    # ---------- 生命周期 ----------

    def start(self) -> None:
        """素材检查 → 动画/工作线程/计时器启动 → 窗口显示（PRD 冷启动<3s）。"""
        # 管理员启动时把本地配置对齐到共享文件：修复旧版本留下的陈旧快照
        # （曾导致本地已是 agnes 的 URL，家人/自己却仍打去旧的 bigmodel）。
        if self._config.assistant.share_family_ai:
            self._shared_ai.save(self._config.ai)
        missing = FrameAssetLoader(self._assets_dir,
                                   self._config.pet.skin).missing_actions()
        if missing:
            logger.warning("asset packs missing: %s (placeholder renderer in use)", missing)
        self._animator.set_state(self._pet.state)
        self._animator.start()
        self._worker.start()
        self._chat_window.reload()        # 启动时把当天历史读回历史窗（窗口仍不显示）
        self._restore_chat_session()      # 同一批记录回灌给模型上下文（宠物「记得」）
        self._set_chat_state(PHASE_IDLE, self._brain_source())
        self._news.start()
        self._cheer.start()
        self._weekly.start()
        # 必须早于问候：先读旧「上次出现」时间戳算间隔，再写入本次启动时间
        self._companion.boot()
        self._hotkey = GlobalHotKey(
            callback=lambda: self._bus.publish(HotkeyChatRequestedEvent()))
        self._hotkey.start()
        self._sync_model_combo()
        self._sync_window()
        self._enter_pet("boot")

        self._tick_timer = QTimer(self._app)
        self._tick_timer.setInterval(TICK_INTERVAL_MS)
        self._tick_timer.timeout.connect(self.tick)
        self._tick_timer.start()

        # 运动计时器默认不跑：宠物静立时不产生任何额外唤醒（不影响办公的硬约束）
        self._motion_timer = QTimer(self._app)
        self._motion_timer.setInterval(MOTION_INTERVAL_MS)
        self._motion_timer.timeout.connect(self.tick_motion)
        logger.info("pet started (assets dir=%s)", self._assets_dir)
        self._log_chat_brain(when="boot")
        self._telemetry.once("first_seen")
        # 自动更新：延迟后台检查（避开冷启动与出场动画；默认关闭，不影响断网用户）
        if self._config.system.auto_check_update and self._config.system.update_repo.strip():
            QTimer.singleShot(2500, self._check_update_background)
        self._telemetry.track("app_start")
        self._greet_on_start()   # 每次打开宠物：后台来一句昂扬问候，不拖冷启动
        if self._onboarding_enabled and not self._onboarding_done():
            # 延后到出场动画之后，别和开机问候/特效抢这一秒
            QTimer.singleShot(900, self._show_onboarding)

    def shutdown(self) -> None:
        """退出前取消在途请求并落盘状态（PRD US-4 离线恢复）。"""
        self._auto_facts.flush()       # 最后几句对话里的事实也尽力抽一次
        self._tray.hide()
        if self._tick_timer is not None:
            self._tick_timer.stop()
        if self._motion_timer is not None:
            self._motion_timer.stop()
        self._animator.stop()
        self._worker.cancel()
        self._worker.wait(2000)
        if getattr(self, "_hotkey", None) is not None:
            self._hotkey.stop()
        effect = getattr(self, "_tech_effect", None)
        if effect is not None:
            effect.stop()
            effect.deleteLater()       # 不再触发 finished，避免触及已销毁的窗口
        self.save_state()

    def tick(self) -> None:
        """周期驱动状态机、提醒与状态落盘（domain 不持时钟）。"""
        now = self._clock.now()
        self._controller.tick(now)
        self._reminder.tick(now)
        self._one_shot.tick(now)
        self._news.tick(now)
        self._cheer.tick(now)
        self._focus.tick(now)
        self._weekly.tick(now)
        self._growth.tick(now)
        self._companion.tick(now)
        self._tasks.tick(now)
        # 天气增强：开启时请求后台抓取（内部节流，零网络阻塞主 tick）
        if hasattr(self, "_weather") and self._weather.is_enabled():
            self._weather.request_fetch()
        self._events.tick(now)
        if hasattr(self, "_situation"):
            self._situation.tick(now)
        # 指标驱动关怀：每天查一次，连续多天无有效互动则主动问候（走 QuietBudget）
        self._maybe_nudge(now)
        self._sync_window()
        self._sync_motion_timer()
        if self._clock.monotonic() - self._last_save_at >= self._config.vitals.save_interval_s:
            self.save_state()

    def tick_motion(self) -> None:
        """高频只推位移：走动/重力落回地面，停下来了就自己关掉（PRD v2.0 C10）。"""
        self._controller.tick_motion(self._clock.now())
        self._sync_window()
        if not self._controller.is_moving():
            self._motion_timer.stop()

    def _sync_motion_timer(self) -> None:
        """按“宠物在不在动”开/关运动计时器，静立时不占 CPU。"""
        if self._motion_timer is None:
            return
        if self._controller.is_moving():
            self._motion_timer.start()
        else:
            self._motion_timer.stop()

    def save_state(self) -> None:
        try:
            self._state_repo.save(self._pet)
        except OSError:
            logger.exception("pet state save failed")
        # 精确「最后出现」时间：独立于会被启动覆盖的 pet_state.last_updated，
        # 周期落盘 + shutdown 兜底，崩溃时最多丢一个保存周期。
        self._presence.touch_seen(self._clock.now())
        self._last_save_at = self._clock.monotonic()

    # ---------- 装配细节 ----------

    def _restore_pet(self) -> Pet:
        """上次快照恢复（位置钳制回合法区）；无快照则落在工作区右下角（PRD US-4/M1）。"""
        x1, y1, x2, y2 = self._screen.bounds_for(PET_SIZE, PET_SIZE)
        pet = self._state_repo.load()
        if pet is None:
            return Pet(pet_id=PET_ID, name=self._config.persona.name,
                       position=PositionVO(x2, y2))
        if not (x1 <= pet.position.x <= x2 and y1 <= pet.position.y <= y2):
            pet.position = pet.position.clamped_to((x1, y1, x2, y2))
        if self._config.pet.stick_to_floor and not getattr(self._config.pet, "roam", True):
            # 上次被丢在半空：启动时一次性落回地面，而不是等重力慢慢掉（PRD v2.0 C10）
            # 自由漫游时不落回地面：猫住在整张桌面，恢复它上次待的高度
            pet.position = PositionVO(pet.position.x, y2)
        # 每次开机都是「元气满满的新一天」：任一维度低于基线一律托底，且不补算关机期间的
        # 离线衰减惩罚。否则旧快照精力/饱食见底时，宠物一睁眼就喊饿喊困求助，
        # 和开机问候自相矛盾（PRD C34/C36）。
        if pet.lift_for_fresh_start(FRESH_START_VITALS):
            logger.info("vitals lifted to %d on fresh start (was low)", FRESH_START_VITALS)
        pet.last_updated = self._clock.now()
        return pet

    def _local_context(self) -> LocalContext:
        """本地小脑的只读上下文：名字 + 三维状态 + 当前小时 + 皮肤（不配 AI 也能聊）。"""
        v = self._pet.vitals
        return LocalContext(name=self._pet.name, hunger=v.hunger, mood=v.mood,
                            energy=v.energy, hour=time.localtime(self._clock.now()).tm_hour,
                            skin=self._config.pet.skin)

    def _system_prompt(self) -> str:
        """人设模板 + 当前心情摘要 + 当前时间 + 可执行操作协议（PRD C3 / M4）。"""
        template = self._config.persona.system_prompt
        try:
            base = template.format(name=self._pet.name,
                                   vitals_summary=self._pet.vitals_summary())
        except (KeyError, IndexError, ValueError):
            # 模板占位符写错也不能崩（PRD 可靠性）：原文 + 状态拼接
            logger.warning("persona template malformed, append summary instead")
            base = f"{template}\n当前状态：{self._pet.vitals_summary()}"
        # 模型自己没有时钟：注入当前时间，否则用户问「几点了」会答不上来
        local = time.localtime(self._clock.now())
        weekday = "一二三四五六日"[local.tm_wday]
        now_line = f"\n当前时间：{time.strftime('%Y-%m-%d %H:%M', local)} 星期{weekday}。"
        phrase = self._companion_phrase()
        phrase_line = f"\n此刻情境：{phrase}。说话与行动都贴合这个情境。" if phrase else ""
        reunion = self._companion.reunion_prompt_hint()
        cfg = self._config.assistant
        knowledge = ""
        if cfg.enabled and cfg.knowledge_enabled and self._pending_query:
            try:
                knowledge = self._knowledge.prompt_context(
                    self._pending_query, top_k=int(cfg.knowledge_top_k))
            except (OSError, ValueError):
                logger.debug("knowledge context failed", exc_info=True)
        # 能力护栏：lite 档附一段「查不到别编造」的约束（非 lite 返回空串）
        ai = self._effective_ai()
        ability = capability_hint(ai.model, infer_tier(ai.model))
        # 长期事实放最后：弱模型对系统提示词尾部更敏感，别被中间的引导语稀释
        anchor = anchor_prompt(self._config.pet.skin,
                               self._config.persona.anchor_enabled)
        depth = self._companion_depth_lines(local)
        return (base + now_line + phrase_line
                + self._companion.prompt_line() + knowledge + reunion
                + self._fact_hygiene_line() + self._memory_guidance_line()
                + self._web_capability_line() + self._image_capability_line()
                + self._video_capability_line()
                + ACTION_PROMPT_GUIDE
                + self._memory.prompt_line(self._pending_query)
                + ability + anchor + depth)

    def _image_capability_line(self) -> str:
        """告诉模型有画图工具：用户明确要图时必须调用，不要只回文字。"""
        if not hasattr(self, "_toolbox") or self._image_ai() is None:
            return ""      # 没配 image 方案就不诱导，免得模型空调用后尴尬
        return ("\n【画图规则·最高优先】用户每次说「画一张」「画个」「生成图片」"
                "「再来一张」「也画个」等想要新图的请求时，无论之前是否画过别的、"
                "无论上下文里有没有图片，都必须立即调用一次 generate_image 工具真正生成，"
                "绝对不能只回一句「画好了」而不调用工具——不调用就等于没画。"
                "只有工具返回了图片路径，才能告诉用户画好了。")

    def _video_capability_line(self) -> str:
        """配了 video 方案就明确声明视频能力，别让模型再说「我不会拍视频」。"""
        if not hasattr(self, "_toolbox") or self._video_ai() is None:
            return ""
        return ("\n【视频规则·最高优先】你已经配置了视频生成模型：用户说"
                "「生成视频」「拍一段」「做个动画」「来段视频」等想要视频时，"
                "必须调用 generate_video 工具真正生成，绝对不要回答「我不会拍视频」"
                "「我只能画图」。视频是异步生成的，可能要几分钟，提交后系统会自动"
                "报告进度；只有工具返回了视频路径，才能告诉用户视频做好了。")

    def _companion_depth_lines(self, local) -> str:
        """情感陪伴深度增量（记忆可见化 / 情绪语气 / 作息共鸣 / 顺势帮忙）。"""
        cfg = self._config
        parts: list[str] = []
        if getattr(cfg.companion, "enabled", True):
            # 第 1 件：记忆自然提起 + 首聊叫名字
            parts.append(memory_visibility_line(self._memory.facts()))
            parts.append(name_first_line(bool(self._memory.values().get("user_name")),
                                         first_message=not bool(self._pending_query)))
            # 第 3 件：心情/精力语气 + 作息共鸣
            vitals = self._pet.vitals
            parts.append(mood_tone_line(vitals.mood, vitals.energy))
            idle_hours = self._companion.gap_hours()
            parts.append(schedule_empathy_line(local.tm_hour, local.tm_wday, idle_hours))
        # 第 5 件：按当前问题顺势浮现能力
        parts.append(discoverability_line(self._pending_query))
        # 近期处境：回复自然贴合用户明说的客观近况（不猜情绪）
        if hasattr(self, "_situation"):
            parts.append(self._situation.prompt_line())
        return "".join(p for p in parts if p)

    def _memory_guidance_line(self) -> str:
        """记忆检索引导：上下文窗口有限，提到旧话题时让模型主动查全量历史。"""
        if not (self._config.assistant.enabled
                and self._config.assistant.tool_calling_enabled):
            return ""
        return ("\n记忆检索：用户说过的稳定事实会自动抽取并出现在提示词末尾的"
                "【长期记忆 · 权威事实】里，回答涉及本人的问题先以它为准。"
                "更早的原话全量存在本机聊天记录，但不在上下文里；用户提到"
                "「之前 / 上次 / 记得吗 / 我说过 / 那天」或这里找不到答案时，"
                "先调 recall 统一检索（它会一次查遍长期记忆、聊天、资料、近况和摘要；"
                "question 用用户提到的实词，可换词多查），基于真实结果回答，"
                "查不到就直说想不起来。需要更精确时再用 search_history / "
                "recall_memories / search_knowledge 单独查。"
                "用户新陈述了重要事实而【长期记忆】里还没有时，可调用 remember 记下。")

    def _brain_source(self) -> str:
        """当前由谁作答：与 ChatService._brain_source 判据一致（只看 api_key）。"""
        return "ai" if self._effective_ai().configured else "local"

    def _fact_hygiene_line(self) -> str:
        """事实边界：弱模型面对模糊输入容易即兴编造具体事实（真机「那年」实测）。"""
        return ("\n事实边界：没有依据的具体事实（年份、日期、数字、店名、地址、"
                "身世经历）一律不要编造，哪怕是开玩笑也不行；"
                "不知道就直说不知道，或者反问用户想聊什么。")

    def _web_capability_line(self) -> str:
        """工具能力声明：抵消历史里「查不到/没能力」旧回复对模型的误导。

        只要「工具调用」总开关开着就声明——天气/位置工具各自独立，不再被
        「联网搜索」单个开关一刀切关掉整段提示（v1.35 修正）。
        """
        if not self._config.assistant.tool_calling_enabled:
            return ""
        has_amap = bool(self._config.web_search.amap_key.strip())
        tip = ("你有 web_search（查网页资讯）、get_location（取所在位置）、"
               "get_weather（查当前天气温度晴雨）、nearby_pois（查附近店铺）"
               "等工具。需要最新信息或本地信息时必须先调工具，不要凭旧对话"
               "说「查不到/没有这个能力」。特别重要：报告具体的餐厅/店铺名、"
               "温度、天气之前，必须来自工具返回的真实结果；没有工具结果时"
               "只能泛泛介绍，绝不能编造店名或数字。"
               "调用 get_weather 后的回复请原样保留工具给出的 markdown 卡片格式，"
               "这类结构化回复不受「不超过100字」的常规限制。")
        if has_amap:
            tip += ("用户已配置高德 Key：问「附近的xx」时先 get_location "
                    "再用 nearby_pois 查真实店铺。")
        else:
            tip += ("查附近店铺需要用户在设置里填高德 Key（免费），"
                    "用户问起时引导配置。")
        return "\n能力提示：" + tip

    def _restore_chat_session(self) -> None:
        """把已落盘的最近对话回灌 ChatSession：重启后宠物仍记得聊过什么。

        只灌内存、不发布任何事件，否则会把旧记录又写一遍盘。`ChatSession.add_turn`
        自带 max_turns 截断，所以多天记录也不会让请求体无限膨胀（FR v1.10 C16）。
        """
        restored = 0
        for record in self._chat_history.recent_turns():
            if record.role not in ("user", "assistant") or record.source == "fallback":
                continue    # system / 兜底话术不是真实对话，别污染上下文
            try:
                self._session.add_turn(record.role, record.content)
                restored += 1
            except ValueError:
                continue
        # 会话摘要（v1.31）：当天更早、已被 max_turns 截掉的轮次补进摘要缓冲，
        # 资料拼够后异步压缩——重启不会「忘了今天开头聊了什么」
        self._summary.reconcile(self._chat_history.today(),
                                [(t.role, t.content) for t in self._session.turns])
        self._chat_input.set_history_count(len(self._chat_history.today()))
        logger.info("chat session restored turns=%d kept=%d max_turns=%d",
                    restored, len(self._session.turns), self._session.max_turns)

    def _observe_summary(self, role: str, content: str) -> None:
        """每轮对话扇出给两个记忆消费者（worker 线程回调，只动内存与落盘）。"""
        self._summary.observe(role, content)
        self._auto_facts.observe(role, content)
        if hasattr(self, "_situation"):
            self._situation.observe(role, content)

    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        """资料索引的语义向量：语义开关关 / 未配 embedding 模型时抛错（降级纯关键词）。"""
        if not self._config.assistant.semantic_search:
            raise ValueError("语义检索未开启")
        return self._ai_client.embeddings(texts, self._effective_ai())

    def _log_chat_brain(self, when: str) -> None:
        """把“这条回复由谁作答”说清楚（PRD v1.6 C8）。

        本地小脑的回复也是逐段上屏，看气泡分不出来源，所以启动与改配置时各提一行。
        """
        ai = self._effective_ai()
        if ai.configured:
            logger.info("chat brain=ai when=%s model=%s base_url=%s", when, ai.model, ai.base_url)
        else:
            logger.info("chat brain=local when=%s（未配置 api_key，回复全由本地小脑作答）", when)

    def _wire_ui(self) -> None:
        self._window.clicked.connect(self._show_chat_input)
        self._window.context_requested.connect(self._menu.popup)
        self._window.moved.connect(self._follow_chat_uis)
        self._chat_input.submitted.connect(
            lambda text: self.send_message(text, self._chat_input.take_attachments()))
        self._chat_window.submitted.connect(
            lambda text: self.send_message(text, self._chat_window.take_attachments(),
                                           channel="history"))
        self._chat_window.model_selected.connect(self._apply_model_profile)
        self._chat_input.model_selected.connect(self._apply_model_profile)
        self._chat_input.history_requested.connect(self._show_history_from_input)
        self._chat_input.active_changed.connect(
            lambda active: self._on_chat_ui_active("input", active))
        self._chat_window.active_changed.connect(
            lambda active: self._on_chat_ui_active("history", active))
        self._screen.watch_changes(self._window.clamp_into_screen)

    def _wire_events(self) -> None:
        self._router.on(BehaviorChangedEvent, lambda _e: self._sync_window())
        # 拖拽松手/动作收场都会改状态，靠它及时拉起或关掉运动计时器，不等下一个 1s
        self._router.on(BehaviorChangedEvent, lambda _e: self._sync_motion_timer())
        self._router.on(SpeakRequestedEvent, self._on_speak)
        self._router.on(ChatReplyChunkEvent, self._on_chunk)
        self._router.on(ChatReasoningEvent, self._on_reasoning)
        self._router.on(MediaTaskEvent, self._on_media_task)
        self._router.on(ChatErrorEvent, self._on_chat_error)
        self._router.on(ReminderDueEvent, self._on_reminder)
        self._router.on(SoftJumpRequestedEvent, self._on_soft_jump)
        self._router.on(HotkeyChatRequestedEvent,
                        lambda _e: self._show_chat_input())
        self._router.on(ChatTurnRecordedEvent, self._on_chat_turn)
        self._router.on(ChatStateChangedEvent, self._on_chat_state)
        self._router.on(UpdateCheckResultEvent, self._on_update_result)
        self._router.on(UpdateDownloadedEvent, self._on_update_downloaded)

    # ---------- 会话记录与状态 ----------

    def _on_chat_turn(self, event: ChatTurnRecordedEvent) -> None:
        """一条完整对话：按天落盘 + 用户问句上屏。

        assistant 回复一律走窗内流式定稿（见 _on_chunk），这里只负责落盘和
        user 问句上屏，避免同一条回复既流式显示又被 receive_record 追加两次。
        """
        record = self._chat_history.record(event)
        if record is None:
            return
        if event.role != "assistant":
            self._chat_window.receive_record(record)
        self._chat_input.set_history_count(self._chat_input.history_count + 1)

    def _on_chat_state(self, event: ChatStateChangedEvent) -> None:
        """会话阶段：输入条与历史窗同步显示思考中/回复中/出错与大脑来源。"""
        self._set_chat_state(event.phase, event.source, event.detail)

    def _set_chat_state(self, phase: str, source: str = "", detail: str = "") -> None:
        """状态只有一个真相，两个界面各自渲染；历史窗即使没打开也保持最新。"""
        self._chat_input.set_state(phase, source, detail)
        self._chat_window.set_state(phase, source, detail)

    def _on_chat_ui_active(self, which: str, active: bool) -> None:
        """任一聊天界面开着就冻结位移；两个都关掉才恢复自主行为。"""
        self._chat_ui_active[which] = active
        self._controller.set_conversation_active(any(self._chat_ui_active.values()))

    # ---------- 交互动作 ----------

    def _show_chat_input(self) -> None:
        """点宠物：输入条贴宠物左侧；历史窗已开着就聚焦它的输入行（FR v1.10 C19）。"""
        if self._chat_window.isVisible():
            self._chat_window.focus_input()
            return
        self._chat_input.show_near(self._window.window_rect())

    def _show_history(self) -> None:
        """右键「历史对话…」：收起单独输入条，打开可继续聊的历史窗；再次点击收起。"""
        self._chat_input.hide()
        self._chat_window.toggle(self._window.window_rect())

    def _show_history_from_input(self) -> None:
        """点输入条的「聊天记录」：打开历史窗并收起输入条（用户主动、不跳变）。"""
        self._chat_input.hide()
        if not self._chat_window.isVisible():
            self._chat_window.show_near(self._window.window_rect())
        else:
            self._chat_window.focus_input()

    def _follow_chat_uis(self) -> None:
        """宠物挪窝（拖拽/走动/重力）后，可见的聊天界面重新侧向贴边（FR v1.10 C18）。

        拖拽小猫时历史窗无视「手动钉住」强制跟随：对话框要贴着猫走。
        """
        rect = self._window.window_rect()
        dragging = self._controller.pet.state is BehaviorState.DRAGGING
        self._chat_input.follow_pet(rect)
        self._chat_window.follow_pet(rect, force=dragging)

    def send_message(self, text: str, attachments=(), channel: str = "input") -> None:
        """入队成功才进入思考 → 预备流式（空白消息不改状态，PRD M4 边界）。

        channel=history 时回复走历史窗内流式，不弹桌面气泡；input 走气泡。

        一句话定时提醒（「5分钟后提醒我开会」）走本地快速通道：正则命中直接排期，
        不等模型——快、省 token，也不受模型不守动作协议的影响（真机实测踩过）。
        带附件时跳过快速通道：图片/文件要走模型（视觉/上下文）。
        """
        attachments = tuple(attachments or ())
        if not str(text or "").strip() and not attachments:
            logger.debug("blank message dropped")
            return
        # 完成信号是「顺带消解」，不阻断正常聊天：惦记与 followup 托付同时收尾
        if is_done_signal(text):
            self._companion.resolve(text)
            self._tasks.resolve(text)
        if not attachments:
            # 快速通道：托付 / 专注 / 取消提醒 / 每日提醒 / 一次性提醒，均不进 AI
            assistant_cfg = self._config.assistant
            if assistant_cfg.enabled and assistant_cfg.tasks_enabled:
                task = parse_task_command(text, now=self._clock.now())
                if task is not None:
                    self._handle_quick_task(text, task)
                    return
            focus_min = parse_focus_command(
                text, default_min=self._config.focus.default_min)
            if focus_min is not None:
                self._start_focus(focus_min)
                return
            if is_cancel_reminders_command(text):
                count = self._one_shot.cancel_all()
                self._controller.speak(
                    "好，全部提醒都清掉啦～" if count else "现在没有挂起的提醒哦。",
                    source="system")
                return
            daily_action = parse_daily_reminder_command(text)
            if daily_action is not None:
                self._handle_quick_reminder(text, daily_action)
                return
            quick_action = parse_reminder_command(text)
            if quick_action is not None:
                self._handle_quick_reminder(text, quick_action)
                return
        # 无参的 system_prompt_provider 拿不到当前问题：缓存一次供 RAG 检索用
        self._pending_query = str(text or "")
        if not self._chat.send(text, attachments, channel=channel):
            logger.debug("blank message dropped")
            return
        self._telemetry.track("msg:sent")
        self._remember_user_facts(text)   # 长期事实（名字/近况）只落本机
        self._growth.on_chat()   # 真实聊天消息计入成长积分
        self._controller.start_thinking()
        self._sync_window()
        self._streaming = True
        # 就地反馈：从哪发、就在哪看回复，绝不自动跳窗（避免「气泡输入→变历史窗」的跳变）。
        # 气泡发的 → 回复留在宠物旁的气泡；历史窗发的 → 回复留在历史窗。
        # 两个界面是同一份落盘记录的两种视图，用户想看全量时主动打开历史窗即可。
        if channel == "history":
            self._chat_window.begin_stream_reply()
        else:
            self._bubble.begin_stream(self._window.bubble_anchor())

    def _handle_quick_reminder(self, text: str, action: RemindAction) -> None:
        """本地快速通道：立即排期 + 双向落聊天记录 + 气泡确认。"""
        self._telemetry.once("first_reminder_done")
        self._one_shot.schedule(action)
        confirm = f"好，{describe_action(action)}，到点我叫你"
        ts = time.time()
        sid = day_key(ts)
        self._bus.publish(ChatTurnRecordedEvent(role="user", content=text,
                                                source="user", ts=ts, session_id=sid))
        self._bus.publish(ChatTurnRecordedEvent(role="assistant", content=confirm,
                                                source="system",
                                                ts=time.time(), session_id=sid))
        self._controller.speak(confirm, source="system")
        logger.info("quick reminder scheduled: %s", describe_action(action))

    def _handle_quick_task(self, text: str, task) -> None:
        """托付快速通道：本地解析直接建任务，双向落聊天记录 + 气泡确认。"""
        accepted = self._tasks.add(task)
        confirm = ("好，我记下了，替你盯着～" if accepted
                   else "手上托付太多啦，先取消几个再来～")
        ts = time.time()
        sid = day_key(ts)
        self._bus.publish(ChatTurnRecordedEvent(role="user", content=text,
                                                source="user", ts=ts, session_id=sid))
        self._bus.publish(ChatTurnRecordedEvent(role="assistant", content=confirm,
                                                source="system",
                                                ts=time.time(), session_id=sid))
        self._telemetry.once("first_task_done")

    def _rebuild_knowledge(self, *, silent: bool = False) -> None:
        """重读用户指定的本机文件夹建索引；失败给出人话而不是静默。"""
        cfg = self._config.assistant
        if not cfg.knowledge_dir:
            if not silent:
                self._report("先在设置 →「体验与隐私」里指定资料文件夹。")
            return
        try:
            result = self._knowledge.build(cfg.knowledge_dir,
                                           now=self._clock.now())
        except (ValueError, OSError) as exc:
            logger.warning("knowledge index build failed: %s", exc)
            if not silent:
                self._report(f"索引没建成：{exc}")
            return
        logger.info("knowledge index rebuilt: %s", result)
        if not silent:
            self._report(f"读完 {result['files']} 个文件、{result['chunks']} 个片段，"
                         f"索引只存在本机。")

    # ---------- v2.0/v2.1：勿扰时段 · 番茄钟 · 统计 · 截图问答 ----------

    def _quiet_deferral(self) -> float:
        """勿扰时段判定：>0 表示距时段结束的秒数（跨零点窗口已处理）。"""
        dnd = self._config.dnd
        if not dnd.enabled:
            return 0.0
        now = time.localtime(self._clock.now())
        hour, minute = now.tm_hour, now.tm_min
        start, end = dnd.start_hour, dnd.end_hour
        in_window = (start <= hour < end if start <= end
                     else hour >= start or hour < end)
        if not in_window:
            return 0.0
        if start <= hour < end:
            end_min = end * 60
        else:                                    # 跨零点：结束时刻在明天
            end_min = (end + 24) * 60
        now_min = hour * 60 + minute
        return max(30.0, (end_min - now_min) * 60.0)

    def _start_focus(self, minutes: float) -> None:
        self._focus.start(minutes)
        self._controller.speak(
            f"好，陪你专注 {minutes:.0f} 分钟，这段时间好好干活，到点我叫你休息～",
            source="system")

    def _on_feed(self) -> None:
        """喂饱：宠物进食动画 + 成长积分（喂食是主要的“照护”行为）。"""
        self._controller.feed()
        self._growth.on_feed()

    def _on_pet_head(self) -> None:
        """抚摸：关系感开启时真的打断当下行为（伸个懒腰蹭回来），否则沿用旧版只加心情。"""
        cfg = self._config.companion
        if cfg.enabled and cfg.interactive:
            self._controller.interact("pet")
        else:
            self._controller.pet_head()
        self._growth.on_pet()

    def _call_pet(self) -> None:
        """叫一声：打断它此刻在做什么，让它抬头看你 + 应一声（命令面板 / 快捷键入口）。"""
        cfg = self._config.companion
        if not (cfg.enabled and cfg.interactive):
            self._controller.speak("我在呢！", source="pet")
            return
        if self._controller.interact("call"):
            self._telemetry.track("feat:user_call")

    def _append_focus_entry(self, entry: dict) -> None:
        path = self._config_repo.path.parent / "focus.json"
        entries = []
        if path.exists():
            try:
                entries = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                entries = []
        entries.append(entry)
        from WisePet.infra.json_store import atomic_write
        atomic_write(path, entries)

    def _load_focus_entries(self) -> list[dict]:
        path = self._config_repo.path.parent / "focus.json"
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (ValueError, OSError):
            return []

    def _show_stats(self) -> None:
        days = self._chat_history.days()
        messages = sum(len(self._chat_history.records_for(day)) for day in days)
        entries = self._load_focus_entries()
        minutes, count = FocusLog.summarize(entries, day_key())
        growth = self._growth.current()
        self._stats_dialog.set_values(days=len(days), messages=messages,
                                      focus_minutes=minutes, focus_count=count,
                                      level=growth.level, stage=growth.stage)
        self._stats_dialog.set_product_metrics(self._telemetry.metrics())
        self._stats_dialog.show_centered()

    def _screenshot_ask(self) -> None:
        """截图问小墨：框选屏幕区域 → 作为图片附件打开聊天窗，用户只需输入问题。

        流程：隐藏宠物和气泡 → 抓全屏快照 → 冻结遮罩上拖拽框选（Esc 取消）→
        裁剪选区为附件 → 恢复宠物并打开历史窗。只抓主屏画面。
        """
        from PySide6.QtCore import QEventLoop

        self._window.hide()
        self._bubble.hide_bubble()
        self._app.processEvents()
        screen = QGuiApplication.primaryScreen()
        snapshot = screen.grabWindow(0) if screen is not None else None
        if snapshot is None or snapshot.isNull():
            self._window.show()
            self._controller.speak("截屏失败了……再试一次？", source="system")
            return

        loop = QEventLoop()
        result: dict = {"rect": None}
        selector = RegionSelector(snapshot, on_done=lambda: self._window.show())
        selector.selected.connect(lambda rect: (result.__setitem__("rect", rect),
                                                loop.quit()))
        selector.cancelled.connect(loop.quit)
        selector.show()
        loop.exec()
        selector.deleteLater()
        self._window.show()

        rect = result.get("rect")
        if rect is None:
            logger.info("screenshot cancelled by user")
            return
        cropped = snapshot.copy(rect)
        att = attachment_from_clipboard_image(cropped.toImage())
        if att is None:
            self._controller.speak("截屏失败了……再试一次？", source="system")
            return
        if self._chat_window.isVisible():
            self._chat_window.add_attachment(att)
        else:
            self._show_history()
            self._chat_window.add_attachment(att)
        self._chat_window.focus_input()
        self._controller.speak("截图放好啦，想问什么直接打字～（图片需要视觉模型）",
                               source="system")

    def _on_speak(self, event: SpeakRequestedEvent) -> None:
        # 打扰预算只拦「程序主动推送」；用户主动发起的回复与工具反馈必须必达
        push = is_push_notification(event.source, event.title)
        logger.info("speak event: source=%s title=%s push=%s preview=%s",
                    event.source, event.title, push, str(event.text)[:30])
        if not self._quiet_budget.allow(event.source, push=push):
            # 无声拦截最容易被误读成「弹窗全没了」：当天第一次额度耗尽给一句解释。
            hint = self._quiet_budget.exhaustion_hint()
            if hint:
                self._controller.start_speaking()
                self._bubble.show_text(
                    hint, source="system", title="提醒额度",
                    anchor=self._window.bubble_anchor(),
                    on_close=self._controller.on_action_finished)
                self._sync_window()
            return
        self._refresh_companion_status()
        if event.source == "reminder":
            self._telemetry.track("reminder_shown")
        with_ack = event.source == "reminder"
        if not with_ack:
            self._controller.start_speaking()
        self._tts.announce(event.text)   # 开了语音就开口朗读（未开为 no-op）
        if self._notify_via_system(event):
            self._sync_window()
            return
        self._bubble.show_text(
            event.text, source=event.source, title=event.title,
            anchor=self._window.bubble_anchor(),
            with_ack=with_ack,
            on_ack=(self._ack_reminder if with_ack else None),
            on_close=self._controller.on_action_finished)
        self._sync_window()

    def _notify_via_system(self, event: SpeakRequestedEvent) -> bool:
        """系统通知中心旁路：开了 use_system 且托盘可用时走系统通知，返回 True。

        否则返回 False 回退自绘气泡（保证通知必达）。提醒类进系统通知后
        不再携带「知道啦」确认（确认语义由下次间隔重排兼顾）。
        """
        if not getattr(self._config.notification, "use_system", False):
            return False
        # tile（通知中心上方的应用名）固定用 WisePet；不设 AUMID 会回落成进程名「python」
        set_notification_app_id("WisePet")
        title = event.title or self._config.persona.name
        msec = int(max(3.0, float(self._config.bubble.duration_s)) * 1000)
        return self._tray.show_notification(title, event.text, msec=msec)

    def _on_reasoning(self, event: ChatReasoningEvent) -> None:
        """推理模型的思考过程：在历史窗的气泡里以浅色小字展示，与正文分开。"""
        self._chat_window.append_reasoning(event.delta)

    def _on_chunk(self, event: ChatReplyChunkEvent) -> None:
        channel = getattr(event, "channel", "input")
        in_window = channel == "history"
        if event.done:
            self._streaming = False
            self._telemetry.once("first_chat_done")
            images = tuple(getattr(event, "images", ()) or ())
            videos = tuple(getattr(event, "videos", ()) or ())
            # agent 工具循环产出的待确认动作：直接走主线程确认链路（跳过 JSON 解析）
            if getattr(event, "pending_action", None) is not None or \
                    parse_action(event.reply) is not None:
                text = event.reply or "好的，我来安排～"
                if in_window:
                    self._chat_window.end_stream_reply(text)
                else:
                    self._bubble.end_stream(text, anchor=self._window.bubble_anchor(),
                                            on_close=self._controller.on_action_finished)
                action = getattr(event, "pending_action", None) or parse_action(event.reply)
                self._handle_action(action)
                return
            reply = event.reply
            self._controller.start_speaking()
            self._tts.announce(reply)
            if in_window:
                self._chat_window.end_stream_reply(reply, images=images, videos=videos)
            else:
                self._bubble.end_stream(reply, anchor=self._window.bubble_anchor(),
                                        on_close=self._controller.on_action_finished,
                                        images=images, videos=videos)
            self._sync_window()
            return
        if event.text_delta:
            if in_window:
                self._chat_window.append_stream_piece(event.text_delta)
            else:
                self._bubble.append_chunk(event.text_delta)

    # ---------- 工具执行层：提醒 / 写文件 / PPT ----------

    def _schedule_remind_action(self, action: RemindAction) -> None:
        """本地小脑解析出的定时提醒（worker 线程回调；只发布事件，不碰 ui）。"""
        self._one_shot.schedule(action)
        logger.info("one-shot reminder scheduled: %s", describe_action(action))

    def _ask_location_consent(self) -> bool:
        """位置授权弹窗（主线程执行；worker 线程经桥阻塞等待结果）。

        三选一：仅本次允许 / 总是允许（写进 config.location.consent）/
        拒绝（同样记住选择，下次不再打扰）。返回 True 表示本次放行。
        """
        # 贴纸风三选一（替代系统 QMessageBox，视觉与设置窗统一）
        box = ChoiceDialog(
            "位置权限",
            "小墨想获取你的大致位置（IP 城市级），用来查「附近」的信息。"
            "只取城市，不会上传聊天内容。",
            [("once", "仅本次允许"), ("always", "总是允许"), ("deny", "拒绝")],
            parent=self._window)
        box.show_centered()
        box.exec()
        choice = box.selected
        if choice == "always":
            self._remember_location_consent("granted")
            return True
        if choice == "deny":
            self._remember_location_consent("denied")
            return False
        return choice == "once"

    def _remember_location_consent(self, consent: str) -> None:
        """把「总是允许 / 拒绝」持久化进 config.json，下次不再弹窗。"""
        try:
            self._config.location.consent = consent
            self._config_repo.save(self._config)
            logger.info("location consent persisted: %s", consent)
        except OSError:
            logger.exception("failed to persist location consent")

    def _handle_action(self, action) -> None:
        """执行模型返回的操作意图：提醒直接排期；写盘类按权限预览→确认→可撤销。"""
        anchor = self._window.bubble_anchor()
        self._controller.start_speaking()
        if isinstance(action, RemindAction):
            self._one_shot.schedule(action)
            self._bubble.end_stream(f"好，{describe_action(action)}，到点我叫你",
                                    anchor=anchor,
                                    on_close=self._controller.on_action_finished)
            self._sync_window()
            return
        permission = permission_required(action, self._config.actions.write_permission)
        if permission == PERMISSION_DENY:
            self._audit.record(type(action).__name__, action.path, "denied")
            self._bubble.end_stream("这个操作被设置挡下了（写盘权限：一律拒绝）",
                                    anchor=anchor,
                                    on_close=self._controller.on_action_finished)
            self._sync_window()
            return
        if permission != PERMISSION_AUTO:
            self._bubble.end_stream(f"稍等，我确认一下：{describe_action(action)}",
                                    anchor=anchor,
                                    on_close=self._controller.on_action_finished)
            self._sync_window()
            approved, remembered = self._confirm_action(action)
            if not approved:
                self._audit.record(type(action).__name__, action.path, "cancelled")
                self._controller.speak("好的，先不动文件。", source="system")
                return
            if remembered and self._config.actions.remember_allowed:
                self.apply_config(replace(
                    self._config,
                    actions=replace(self._config.actions,
                                    write_permission=PERMISSION_AUTO)))
        receipt = self._snapshot_before_write(action)
        try:
            saved = (self._write_file(action)
                     if isinstance(action, CreateFileAction)
                     else build_ppt(action.path, action.title,
                                    [dict(slide) for slide in action.slides]))
        except OSError as exc:
            self._audit.record(type(action).__name__, action.path, "failed")
            logger.warning("action write failed: %s", exc)
            self._controller.speak(f"呜，没写成（{exc}）", source="system")
            return
        self._audit.record(type(action).__name__, str(saved), "ok")
        self._last_action_receipt = receipt
        self._menu.set_undo_enabled(True)
        self._controller.speak(f"搞定啦，文件在这里：{saved}（右键可撤销）",
                               source="system")

    def _snapshot_before_write(self, action) -> dict:
        """写盘前留一份可还原快照：新文件记「原本不存在」，覆盖写记原始字节。"""
        path = Path(action.path).expanduser()
        if path.suffix == "":
            path = path.with_suffix(".txt")
        existed = path.exists()
        raw = None
        if existed:
            try:
                raw = path.read_bytes()
            except OSError:
                logger.warning("cannot snapshot existing file: %s", path)
        return {"path": path, "existed": existed, "raw": raw}

    def _undo_last_action(self) -> None:
        receipt = self._last_action_receipt
        if receipt is None:
            return
        path: Path = receipt["path"]
        if receipt["existed"] and receipt["raw"] is None:
            self._controller.speak("原文件读不出来，我不敢乱动，先不撤销了。",
                                   source="system")
            return
        try:
            if receipt["existed"]:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(receipt["raw"])
            elif path.exists():
                path.unlink()
        except OSError as exc:
            logger.warning("action undo failed: %s", exc)
            self._controller.speak(f"呜，撤销没成功（{exc}）", source="system")
            return
        self._last_action_receipt = None
        self._menu.set_undo_enabled(False)
        self._controller.speak("已经帮你还原啦。", source="system")

    def _write_file(self, action: CreateFileAction):
        path = Path(action.path).expanduser()
        if path.suffix == "":
            path = path.with_suffix(".txt")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(action.content, encoding="utf-8")
        logger.info("file written: %s (%d chars)", path, len(action.content))
        return path

    def _confirm_action(self, action) -> tuple[bool, bool]:
        """写盘前的预览确认：完整内容 + 风险提示 + 可选「以后都允许」。"""
        is_file = isinstance(action, CreateFileAction)
        dialog = ActionConfirmDialog(
            f"创建文件：{action.path}" if is_file else f"生成 PPT：{action.path}",
            action_preview(action)[:4000], self._window)
        dialog.setWindowTitle("WisePet 想创建文件" if is_file else "WisePet 想生成 PPT")
        dialog.set_remember_enabled(self._config.actions.remember_allowed)
        dialog.exec()
        return dialog.result_value()

    def _generate_cheer(self) -> str | None:
        """鼓励语生成：AI 在线就出一句古诗名句/夸奖；未配置或失败返回 None（走本地语录）。"""
        ai = self._effective_ai()
        if not ai.configured:
            return None
        prompt = (f"你是桌面宠物「{self._pet.name}」。随机来一句给主人的积极话语："
                  f"可以是一句古诗名句（带出处人名）或一句真诚的夸奖，"
                  f"不超过40字，直接输出正文，不要解释，不要重复上一次的内容：")
        try:
            return self._ai_client.complete([{"role": "user", "content": prompt}], ai)
        except AIGatewayError as exc:
            logger.warning("cheer generate failed [%s]: %s", exc.kind, exc.detail)
            return None

    def _generate_greeting(self) -> str | None:
        """启动问候口号：AI 在线就生成一句积极昂扬的开场口号；未配置/失败返回 None。

        问候是「开机第一眼」的体验，绝不能因为等 AI 而迟迟不出：这里把本次请求的
        超时钳到 GREETING_TIMEOUT_S（远小于聊天默认的 30s），网关慢/不通时快速放弃、
        由 _greeting_text 回落兜底语，保证问候几秒内必达。
        """
        ai = self._effective_ai()
        if not ai.configured:
            return None
        hint = self._companion.reunion_prompt_hint()
        prompt = (f"你是桌面宠物「{self._pet.name}」，主人刚把你打开。{hint}"
                  f"来一句积极昂扬、简短有力的开场口号，唤醒主人元气满满地开启新一天，"
                  f"不超过30字，直接输出正文，不要解释：")
        greet_ai = replace(ai, timeout_s=min(ai.timeout_s, GREETING_TIMEOUT_S))
        try:
            slogan = self._ai_client.complete([{"role": "user", "content": prompt}], greet_ai)
        except AIGatewayError as exc:
            logger.warning("greeting generate failed [%s]: %s", exc.kind, exc.detail)
            return None
        slogan = str(slogan or "").strip()
        return slogan[:60] or None

    def _greeting_text(self) -> str:
        """问候正文：久别重逢优先（本地确定、秒出）；否则 AI 口号 / 兜底语。

        久别时不再等 AI：用户要的是「它真的说得出这几天你没在」，
        而不是 2 秒后一句可能完全没提这件事的口号。
        """
        reunion = self._companion.reunion_text()
        if reunion:
            return reunion
        return (self._generate_greeting()
                or STARTUP_GREETING_FALLBACK.format(name=self._pet.name))

    def _greet_on_start(self) -> None:
        """每次打开宠物：后台生成一句昂扬口号并推送，不阻塞冷启动（PRD <3s）。"""

        def _work() -> None:
            try:
                text = self._greeting_text()
            except Exception:  # noqa: BLE001 - 问候必达，任何异常都退回兜底语
                logger.exception("greeting generation failed")
                text = STARTUP_GREETING_FALLBACK.format(name=self._pet.name)
            title = ("好久不见" if self._companion.reunion_text()
                     else "开启新的一天")
            self._bus.publish(SpeakRequestedEvent(text, source="cheer",
                                                  title=title))

        threading.Thread(target=_work, name="wisepet-greet", daemon=True).start()

    def _summarize_news(self, headlines: list[str]) -> str | None:
        """新闻标题 → AI 摘要；未配 Key 或失败时返回 None（走纯标题播报）。"""
        ai = self._effective_ai()
        if not ai.configured:
            return None
        listing = "\n".join(f"- {title}" for title in headlines)
        prompt = ("下面是刚抓取的科技资讯标题。挑最有信息量的内容写成一段"
                  "不超过120字、口语化的中文新闻速览，直接输出正文：\n" + listing)
        try:
            return self._ai_client.complete([{"role": "user", "content": prompt}], ai)
        except AIGatewayError as exc:
            logger.warning("news summarize failed [%s]: %s", exc.kind, exc.detail)
            return None

    def _summarize_week(self, stats: dict) -> str | None:
        """本周统计 → AI 润色成宠物口吻的周报；未配 Key 或失败返回 None（走本地模板）。"""
        ai = self._effective_ai()
        if not ai.configured:
            return None
        facts = [f"本周我们聊了 {stats.get('messages', 0)} 句",
                 f"有 {stats.get('active_days', 0)} 天你在"]
        if stats.get("top_topic"):
            facts.append(f"你问得最多的是「{stats['top_topic']}」")
        prompt = (f"你是桌面宠物「{self._pet.name}」。根据本周陪伴数据写成一段"
                  f"不超过80字、温暖口语化的中文周报，直接输出正文，不要解释：\n"
                  + "，".join(facts))
        try:
            return self._ai_client.complete([{"role": "user", "content": prompt}], ai)
        except AIGatewayError as exc:
            logger.warning("weekly summarize failed [%s]: %s", exc.kind, exc.detail)
            return None

    def _on_chat_error(self, event: ChatErrorEvent) -> None:
        """失败不弹窗阻塞：兜底话术 + 走说话动画（PRD US-3/M4）。

        错误事件可能由网关直接发布（不经过 ChatService._on_failure），所以状态栏
        在这里也兜一次，保证「出错了 + 错误类别」一定看得见。
        """
        self._streaming = False
        self._telemetry.track("model:fail")
        self._controller.end_thinking()           # 失败也要结束思考保持，别一直挂着
        self._set_chat_state(PHASE_ERROR, detail=event.detail or event.kind)
        fallback = ChatService.fallback_text(event.kind, event.detail)
        # 就地收尾：哪条通道正在流式，就在哪里把占位定稿成错误话术，不跳窗
        channel = getattr(event, "channel", "input")
        if channel == "history" and self._chat_window.is_streaming_reply:
            self._chat_window.end_stream_reply(fallback)
        elif channel != "history":
            self._bubble.end_stream(fallback, anchor=self._window.bubble_anchor(),
                                    on_close=self._controller.on_action_finished)
        self._controller.speak(fallback, source="fallback")

    def _on_reminder(self, event: ReminderDueEvent) -> None:
        self._reminder_kind = event.kind
        cursor = QCursor.pos()
        self._controller.move_to(cursor.x() - PET_SIZE // 2, cursor.y() - PET_SIZE // 2)
        title = "喝水提醒" if event.kind == KIND_WATER else "久坐提醒"
        self._announce_reminder(event.text, title=title)

    def _announce_reminder(self, text: str, title: str = "提醒") -> None:
        """提醒播报：配了 AI 就后台润色成人设口吻；失败/未配置退回原文（必响）。"""
        ai = self._effective_ai()
        if not ai.configured:
            self._bus.publish(SpeakRequestedEvent(text, source="reminder", title=title))
            return

        def _publish(polished: str | None) -> None:
            self._bus.publish(SpeakRequestedEvent((polished or text)[:160],
                                                  source="reminder", title=title))

        def _work() -> None:
            try:
                _publish(self._polish_reminder_text(text))
            except Exception:  # noqa: BLE001 - 线程兜底：原文也要出
                logger.exception("reminder polish thread failed")
                _publish(None)

        threading.Thread(target=_work, name="wisepet-remind-polish",
                         daemon=True).start()

    def _polish_reminder_text(self, text: str) -> str | None:
        """把提醒话术润色成宠物人设口吻；未配 Key 或失败返回 None（回落原文）。"""
        ai = self._effective_ai()
        if not ai.configured:
            return None
        prompt = (f"你是桌面宠物「{self._pet.name}」，要用俏皮亲切的口吻提醒主人。"
                  f"把下面这条提醒改写成一句不超过40字的中文。"
                  f"只能使用原文给出的事项，严禁编造原文没有的时间点、日期或数字"
                  f"（例如原文没说几点就绝不能提几点）；直接输出正文，不要解释：\n{text}")
        try:
            polished = self._ai_client.complete([{"role": "user", "content": prompt}], ai)
        except AIGatewayError as exc:
            logger.warning("reminder polish failed [%s]: %s", exc.kind, exc.detail)
            return None
        polished = str(polished or "").strip()
        return polished or None

    def _ack_reminder(self) -> None:
        # 「知道啦」既算一次关闭（护栏），也算一次确认（北极星的有效互动）
        self._telemetry.track("reminder_dismissed")
        self._telemetry.track("reminder:confirmed")
        if self._reminder_kind is not None:
            self._reminder.notify_ack(self._reminder_kind)
            self._reminder_kind = None
        self._controller.on_action_finished()

    def open_settings(self) -> None:
        dialog = SettingsDialog(self._config, on_saved=self.apply_config,
                                skin_options=self._available_skins(),
                                screen_bounds=self._screen,
                                parent=self._window,
                                is_manager=self._is_manager(),
                                profile_name=self._profile,
                                on_change_pin=self._change_my_pin,
                                current_data_dir=str(self._base_dir),
                                on_data_dir_change=self._change_data_dir)
        dialog.set_update_callback(self._check_update_background)
        dialog.exec()

    def _change_data_dir(self, target: str) -> str:
        """改数据存储位置：校验 → 确认 → 复制旧数据 → 写指针 → 提示重启。

        返回给用户看的结果文案。不删除旧目录（作备份）；运行中仓储已绑定旧路径，
        必须重启才真正切到新位置。
        """
        from pathlib import Path as _Path
        candidate = _Path(str(target or "")).expanduser()
        if not candidate.is_absolute() or not candidate.is_dir():
            return "失败：目标必须是已存在的绝对路径文件夹"
        old = self._base_dir.resolve()
        new = candidate.resolve()
        if new == old:
            return "取消：目标就是当前位置"
        if str(new).startswith(str(old) + "\\") or str(new).startswith(str(old) + "/"):
            return "失败：不能把数据目录放进当前目录内部"
        answer = QMessageBox.question(
            self._window, "修改数据存储位置",
            f"把 WisePet 的全部数据从：\n{old}\n\n复制到：\n{new}\n\n"
            "原目录会保留作备份；复制完成后需要重启 WisePet 才会切到新位置。\n"
            "确定继续吗？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return "取消：没有改动"
        copied = migrate_data(old, new)
        if not save_data_dir(str(new)):
            return "失败：数据已复制，但写位置指针失败，请检查权限"
        logger.info("data dir changed: %s -> %s (%d files copied, restart required)",
                    old, new, len(copied))
        QMessageBox.information(
            self._window, "需要重启",
            f"已复制 {len(copied)} 个文件到新位置。\n"
            "请完全退出 WisePet 后重新启动，才会使用新位置。\n"
            "旧目录保留作备份，确认一切正常后可自行删除。")
        return f"成功：已迁移 {len(copied)} 个文件，请重启 WisePet"

    def _change_my_pin(self) -> None:
        """当前身份的 PIN 设置/修改/解除（picker 与设置窗共用同一流程）。"""
        from WisePet.ui.profile_picker import change_pin_flow
        if change_pin_flow(self._window, ProfileStore(self._base_dir),
                           self._profile):
            self._controller.speak("PIN 已经更新好啦～", source="pet")

    def _is_manager(self) -> bool:
        """管理员 = 默认身份（最早使用、不可删除）；管理动作（共享 AI）专属。"""
        return ProfileStore(self._base_dir).is_manager(self._profile)

    def _profile_name(self) -> str:
        return ProfileStore(self._base_dir).active()

    def _available_skins(self) -> list[str]:
        """素材齐全的皮肤（没生成狗/猴素材时设置窗不会列出它们）。"""
        return FrameAssetLoader(self._assets_dir).available_skins()

    def reload_assets(self) -> None:
        """丢掉内存里的帧图重读磁盘：换素材后不用重启就能看到效果。

        FrameAnimator 按文件路径缓存 QPixmap，替换同名 PNG 路径不变，不主动清缓存
        就一直在播旧帧——换 AI 图时这个比重启便宜得多。
        """
        self._animator.set_loader(FrameAssetLoader(self._assets_dir, self._config.pet.skin))
        logger.info("assets reloaded from %s (skin=%s)", self._assets_dir, self._config.pet.skin)

    def apply_config(self, config: AppConfig) -> None:
        """保存后热生效：配置替换 + 会话上限 + 提醒重新排期 + 皮肤热切换。"""
        previous_skin = self._config.pet.skin
        previous_system = self._config.system
        previous_assistant = self._config.assistant
        previous_ai = self._config.ai
        self._config = config
        self._config_repo.save(config)
        self._set_chat_state(PHASE_IDLE, self._brain_source())
        self._session.max_turns = config.pet.max_turns
        if config.pet.skin != previous_skin:
            self._animator.set_loader(FrameAssetLoader(self._assets_dir, config.pet.skin))
            logger.info("skin switched: %s -> %s", previous_skin, config.pet.skin)
        for kind, item in ((KIND_WATER, config.reminders.water),
                           (KIND_SEDENTARY, config.reminders.sedentary)):
            self._reminder.set_enabled(kind, item.enabled)
            self._reminder.set_interval(kind, float(item.interval_min))
        self._news.reschedule()      # 新闻间隔变化立即生效，不等旧周期
        self._cheer.reschedule()     # 鼓励间隔同理
        # 资料文件夹/开关变了就重建索引；关掉时清掉旧索引，不残留用户文件片段
        if config.assistant != previous_assistant:
            if not config.assistant.enabled or not config.assistant.knowledge_enabled:
                self._knowledge.clear()
            elif config.assistant.knowledge_dir:
                self._rebuild_knowledge(silent=True)
        # 家庭共享 AI（管理员专属）：开启后 AI 配置一变就同步写全局共享文件，
        # 家人才跟着用上新 Key/端点；只在勾选翻转时写一次会让家人一直用旧快照。
        if config.assistant.share_family_ai:
            if (config.ai != previous_ai
                    or not previous_assistant.share_family_ai):
                self._shared_ai.save(config.ai)
                logger.info("family shared AI updated by manager: model=%s",
                            config.ai.model)
        elif previous_assistant.share_family_ai:
            self._shared_ai.clear()
            logger.info("family shared AI cleared")
        logger.info("config applied: model=%s skin=%s walk=%.0fpx/s",
                    config.ai.model, config.pet.skin, config.pet.walk_speed_px_per_s)
        self._sync_model_combo()
        self._menu.set_muted(config.quiet.muted)
        self._menu.set_undo_enabled(self._last_action_receipt is not None)
        self._refresh_companion_status()
        self._memory.hydrate(getattr(self._local, "_engine", None))
        # 系统配置（日志级别 / 分割阈值 / 保留天数 / 分档）保存后热重建，不用重启
        if config.system != previous_system:
            reconfigure_logging(config.system, self._config_repo.path.parent)
            logger.info("system log settings applied: level=%s max_mb=%s retention=%s split=%s",
                        config.system.log_level, config.system.log_max_mb,
                        config.system.log_retention_days,
                        config.system.log_split_by_level)
        self._log_chat_brain(when="config-saved")

    # ---------- 多模型方案 ----------

    def _sync_model_combo(self) -> None:
        """聊天窗/输入条下拉 = 方案列表；选中项 = 当前生效配置。"""
        names = [p.name for p in self._config.ai_profiles]
        active = self._active_profile_name()
        self._chat_window.set_models(names, active)
        self._chat_input.set_models(names, active)

    def _active_profile_name(self) -> str:
        # 只按 base_url + model 匹配：家庭共享会替换 api_key，用 key 比必然对不上，
        # 下拉会错误回退到第一个方案，看起来像「切了又被弹回去」。
        ai = self._effective_ai()
        for profile in self._config.ai_profiles:
            if (profile.base_url == ai.base_url and profile.model == ai.model
                    and normalize_purpose(getattr(profile, "purpose", ""))
                    == normalize_purpose(getattr(ai, "purpose", ""))):
                return profile.name
        return names[0] if (names := [p.name for p in self._config.ai_profiles]) else ""

    def _apply_model_profile(self, name: str) -> None:
        """聊天窗切模型：只换 base_url/key/model 三件套，温度超时等保持不动。"""
        profile = next((p for p in self._config.ai_profiles if p.name == name), None)
        if profile is None:
            logger.debug("model profile not found: %s", name)
            return
        ai = replace(self._config.ai, base_url=profile.base_url,
                     api_key=profile.api_key, model=profile.model,
                     embedding_model=profile.embedding_model,
                     purpose=normalize_purpose(getattr(profile, "purpose", "")))
        if ai == self._config.ai:
            return
        logger.info("model switched via chat window: %s (%s)", name, profile.model)
        self.apply_config(replace(self._config, ai=ai))
        effective = self._effective_ai()
        if effective.model != profile.model or effective.base_url != profile.base_url:
            logger.warning("model switch masked by effective config: want=%s@%s "
                           "effective=%s@%s", profile.model, profile.base_url,
                           effective.model, effective.base_url)

    def toggle_visible(self) -> None:
        visible = self._window.isVisible()
        if visible:
            # 退场粒子在宠物原位爆散（纯装饰，隐藏本身立即生效）
            self._play_tech_effect("exit")
        self._window.setVisible(not visible)
        self._menu.set_hidden_state(hidden=visible)
        self._tray.set_hidden_state(hidden=visible)
        if visible:
            # 藏起来就没人在看：停掉帧轮询（每秒 4~16 次的取帧 + 镜像 + 重绘请求），
            # 只留 1s 主 tick 继续跑衰减与落盘（PRD NFR：空闲 CPU <3%）
            self._animator.stop()
            self._chat_input.hide()
            self._chat_window.hide()
            self._bubble.hide_bubble()
        else:
            self._animator.start()
            self._sync_window()
            self._enter_pet("toggle")

    # ---------- 宠物登场/退场特效 ----------

    def _enter_kind_for(self, trigger: str) -> str:
        """登场特效种类：

        - 配置为具体 kind → 固定用之
        - 配置为 ``random`` → 每次从 7 种里随机（ink 已重做为能量波纹，不再排除）
        - 配置为 ``auto``（默认）→ 按场景分派；其中「从托盘恢复」(toggle)
          随机选一种，满足「隐藏/出现也要随机」的诉求，其余场景仍固定可解释
        """
        pref = getattr(self._config.pet, "enter_effect", "auto")
        if pref == "random":
            return random_enter_kind()
        if pref in ENTER_KINDS:
            return pref
        if trigger == "toggle":
            return random_enter_kind()
        return TRIGGER_ENTER_KIND.get(trigger, "spawn")

    def _enter_pet(self, trigger: str, after: Callable[[], None] | None = None) -> None:
        """统一登场：窗口透明→播放对应进入特效→结束淡入（after 在淡入后执行）。"""
        kind = self._enter_kind_for(trigger)
        self._window.setWindowOpacity(0.0)
        self._window.show()
        self._window.raise_()

        def _on_done() -> None:
            self._fade_in_pet()
            if after is not None:
                after()

        self._play_tech_effect(kind, on_done=_on_done)

    def _play_effect_only(self, trigger: str) -> None:
        """宠物已可见时，仅播放对应特效层作「随触发」反馈，不重播登场（不闪烁）。"""
        kind = self._enter_kind_for(trigger)
        prev = getattr(self, "_tech_effect", None)
        if prev is not None:
            prev.stop()      # 仅清掉上一个反馈层，不影响宠物显形
        self._play_tech_effect(kind)

    def _play_or_enter(self, trigger: str) -> None:
        """触发场景：隐藏则登场（带特效+淡入），已可见则仅播特效层作反馈。"""
        if not self._window.isVisible():
            self._enter_pet(trigger)
        else:
            self._play_effect_only(trigger)

    def _play_tech_effect(self, kind: str, on_done: Callable[[], None] | None = None) -> None:
        """在宠物中心播放特效；kind 决定样式（进入种类 / "exit" 退场），on_done 结束后回调。"""
        center = self._window.frameGeometry().center()
        effect = TechEffect(center, kind=kind)
        self._tech_effect = effect             # 持引用防回收
        if on_done is not None:
            effect.finished.connect(on_done)
        effect.start()

    def _fade_in_pet(self) -> None:
        """出场特效结束：宠物从透明淡入现身（220ms）。"""
        self._fade_window(0.0, 1.0, 220)

    def _fade_window(self, from_value: float, to_value: float, ms: int,
                     on_done: Callable[[], None] | None = None) -> None:
        from PySide6.QtCore import QPropertyAnimation
        anim = QPropertyAnimation(self._window, b"windowOpacity", self._window)
        anim.setDuration(ms)
        anim.setStartValue(from_value)
        anim.setEndValue(to_value)
        if on_done is not None:
            anim.finished.connect(on_done)
        anim.start()
        self._fade_anim = anim                 # 持引用防回收

    def _exit_app(self) -> None:
        """右键/托盘退出：先播退场特效再真正退出（窗口已隐藏时直接退）。"""
        if self._window.isVisible():
            self._play_tech_effect("exit", on_done=self._app.quit)
        else:
            self._app.quit()

    def _sync_window(self) -> None:
        # C++ 对象可能先于 Python 包装被销毁（teardown 竞态/系统销毁窗口）：
        # 此刻一切 move/anchor 都没有意义，跳过并让运动计时器自行关闭
        if not isValid(self._window):
            self._motion_timer and self._motion_timer.stop()
            return
        self._window.sync_from_pet()
        # 气泡只要开着就跟着宠物走：提醒/求助/回复都不脱离猫的位置（跟随状态）
        if self._bubble.isVisible():
            self._bubble.follow(self._window.bubble_anchor())

    def _on_soft_jump(self, event: SoftJumpRequestedEvent) -> None:
        """自由漫游瞬移：淡出→落位→淡入由宠物窗完成，完毕后解除控制器冻结。"""
        self._window.soft_jump_to(event.x, event.y,
                                  on_done=self._controller.finish_soft_jump)

    # ---------- 供测试/诊断读取 ----------

    @property
    def pet(self) -> Pet:
        return self._pet

    @property
    def controller(self) -> PetController:
        return self._controller

    @property
    def window(self) -> PetWindow:
        return self._window


def select_startup_profile(profiles, requested: str) -> str | None:
    """启动身份选择：返回身份名；返回 None 表示用户取消（Esc/关闭），不启动。

    - `--profile=名字` 显式指定时不弹窗，取消语义不适用；
    - 没有自建身份且无 active 的老用户直接进入默认身份，零打扰；
    - 弹了「谁来了？」就必须**显式选一个**（或点「沿用上次身份」）；
      Esc / 关窗一律取消启动，不再隐式落到管理员身份（真机 bug 修复）。
    """
    current = requested or profiles.active()
    if requested or not (profiles.list() or current):
        return current
    picker = ProfilePickerDialog(profiles, current=current)
    if picker.exec() != QDialog.DialogCode.Accepted or picker.selected is None:
        return None
    name = picker.selected
    if name:
        profiles.set_active(name)
    else:
        profiles.clear_active()
    return name


def main() -> int:
    """入口：身份（profile）→ 单实例互斥 → 日志 → 装配 → 启动 → 事件循环。"""
    started_at = time.monotonic()
    app = QApplication(sys.argv)
    app.setApplicationName("WisePet")

    # 本地身份（v1.31）：`--profile=名字` 优先，否则用上次切换时记住的名字；
    # 空身份 = 默认数据目录（老用户零迁移）
    base_dir = resolve_base_dir()           # 自定义位置：环境变量 > 指针文件 > 默认目录
    profiles = ProfileStore(base_dir)
    requested = ""
    for arg in sys.argv[1:]:
        if arg.startswith("--profile="):
            try:
                requested = safe_profile_name(arg.split("=", 1)[1])
            except ValueError as exc:
                QMessageBox.warning(None, "WisePet", f"身份名不合法：{exc}")
                return 1
    profile_name = select_startup_profile(profiles, requested)
    if profile_name is None:                # Esc / 关窗：不进任何身份，直接退出
        return 0
    if profile_name:
        profiles.ensure(profile_name)        # 先建目录，迁移与配置都落到里面

    global _instance_guard
    _instance_guard = InstanceGuard(port=guard_port_for(profile_name))
    if not _instance_guard.acquire():
        QMessageBox.warning(None, "WisePet", "已经有一个桌宠在跑啦，不再重复启动。")
        return 1

    data_dir = profiles.data_dir_for(profile_name)
    migrated = migrate_legacy_data(data_dir)
    # 日志装配要先读到「系统配置」分区里的级别/阈值/保留期，再挂 handler
    config_repo = JsonConfigRepository(data_dir)
    boot_config = config_repo.load()
    log_file = setup_logging(system=boot_config.system, data_dir=data_dir)
    logger.info("bootstrapping (log=%s, profile=%s)", log_file, profile_name or "默认")
    if migrated:
        logger.info("legacy data migrated from ~/.ai-bot: %s", migrated)

    # 首次运行判定放在真实入口：组装测试构造 PetApplication 时 onboarding 默认 False
    onboarding = not (config_repo.path.parent / "onboarding.done").exists()
    pet_app = PetApplication(app, onboarding=onboarding,
                             config_repo=config_repo, profile=profile_name,
                             base_dir=base_dir)
    app.aboutToQuit.connect(pet_app.shutdown)
    pet_app.start()
    logger.info("cold start took %.2fs (target <3s)", time.monotonic() - started_at)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
