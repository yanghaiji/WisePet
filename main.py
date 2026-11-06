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

from PySide6.QtCore import QTimer
from PySide6.QtGui import QCursor, QGuiApplication                    # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox               # noqa: E402

from WisePet.app.action_protocol import (                               # noqa: E402
    ACTION_PROMPT_GUIDE,
    CreateFileAction,
    MakePptAction,
    RemindAction,
    describe_action,
    is_cancel_reminders_command,
    parse_action,
    parse_daily_reminder_command,
    parse_focus_command,
    parse_reminder_command,
)
from WisePet.app.cheer_service import CheerService                      # noqa: E402
from WisePet.app.cheer_service import CheerService                      # noqa: E402
from WisePet.app.chat_history import ChatHistory                        # noqa: E402
from WisePet.app.chat_service import ChatService                       # noqa: E402
from WisePet.app.event_bus import EventBus                              # noqa: E402
from WisePet.app.focus_service import FocusLog, FocusService           # noqa: E402
from WisePet.app.growth_service import GrowthService                    # noqa: E402
from WisePet.app.local_responder import LocalContext, LocalResponder    # noqa: E402
from WisePet.app.news_service import NewsService                       # noqa: E402
from WisePet.app.one_shot_reminders import OneShotReminders             # noqa: E402
from WisePet.app.pet_controller import PetController                   # noqa: E402
from WisePet.app.reminder_service import ReminderService               # noqa: E402
from WisePet.app.tts_announcer import TtsAnnouncer                      # noqa: E402
from WisePet.app.weekly_report import WeeklyReportService              # noqa: E402
from WisePet.domain.chat_record import day_key                          # noqa: E402
from WisePet.domain.chat_session import ChatSession                    # noqa: E402
from WisePet.domain.events import (                                    # noqa: E402
    PHASE_ERROR,
    PHASE_IDLE,
    BehaviorChangedEvent,
    ChatErrorEvent,
    ChatReplyChunkEvent,
    ChatStateChangedEvent,
    ChatTurnRecordedEvent,
    KIND_SEDENTARY,
    KIND_WATER,
    ReminderDueEvent,
    SoftJumpRequestedEvent,
    HotkeyChatRequestedEvent,
    SpeakRequestedEvent,
)
from WisePet.domain.config_model import AppConfig                       # noqa: E402
from WisePet.domain.pet import Pet                                     # noqa: E402
from WisePet.domain.position import PositionVO                         # noqa: E402
from WisePet.domain.state_machine import BehaviorState                  # noqa: E402
from WisePet.domain.errors import AIGatewayError                        # noqa: E402
from WisePet.domain.vitals import VitalsVO                              # noqa: E402
from WisePet.infra.chat_log_repo import ChatLogRepository               # noqa: E402
from WisePet.infra.config_repo import (                               # noqa: E402
    JsonConfigRepository, migrate_legacy_data)
from WisePet.infra.frame_loader import FrameAssetLoader                # noqa: E402
from WisePet.infra.global_hotkey import GlobalHotKey                   # noqa: E402
from WisePet.infra.growth_store import GrowthStore                     # noqa: E402
from WisePet.infra.instance_guard import InstanceGuard                 # noqa: E402
from WisePet.infra.logger_setup import setup_logging                   # noqa: E402
from WisePet.infra.news_feed import fetch_headlines                    # noqa: E402
from WisePet.infra.reminder_store import ReminderStore                 # noqa: E402
from WisePet.infra.tts import create_synthesizer                       # noqa: E402
from WisePet.infra.weekly_store import WeeklyStore                     # noqa: E402
from WisePet.infra.openai_client import OpenAICompatClient             # noqa: E402
from WisePet.infra.ppt_builder import build_ppt                        # noqa: E402
from WisePet.infra.pet_state_repo import PetStateRepository            # noqa: E402
from WisePet.infra.system_clock import SystemClock                     # noqa: E402
from WisePet.ui.ai_worker import AiWorker                              # noqa: E402
from WisePet.ui.chat_input import ChatInput                             # noqa: E402
from WisePet.ui.chat_text_edit import attachment_from_clipboard_image   # noqa: E402
from WisePet.ui.chat_window import ChatWindow                           # noqa: E402
from WisePet.ui.frame_animator import CANVAS_SIZE, FrameAnimator       # noqa: E402
from WisePet.ui.pet_window import PetWindow                            # noqa: E402
from WisePet.ui.qt_bridge import EventRouter, QtEventBridge            # noqa: E402
from WisePet.ui.region_selector import RegionSelector                   # noqa: E402
from WisePet.ui.screen_bounds import ScreenBounds                     # noqa: E402
from WisePet.ui.settings_dialog import SettingsDialog                  # noqa: E402
from WisePet.ui.speech_bubble import SpeechBubble                      # noqa: E402
from WisePet.ui.stats_dialog import StatsDialog                        # noqa: E402
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

#: 进程存活期必须持有单实例守卫，否则命名管道/端口会被回收释放
_instance_guard: InstanceGuard | None = None


class PetApplication:
    """把装配、事件订阅、定时器与生命周期收在一处，便于整体冒烟测试。"""

    def __init__(self, app: QApplication,
                 config_repo: JsonConfigRepository | None = None,
                 state_repo: PetStateRepository | None = None,
                 assets_dir: Path | str | None = None,
                 clock: SystemClock | None = None,
                 chat_log_repo: ChatLogRepository | None = None,
                 tray_available: bool | None = None):
        self._app = app
        # 宠物藏起来后进程没有任何可见窗口；不关掉这个开关，事件循环会跟着退出
        self._app.setQuitOnLastWindowClosed(False)
        self._config_repo = config_repo or JsonConfigRepository()
        self._state_repo = state_repo or PetStateRepository()
        self._clock = clock or SystemClock()
        self._assets_dir = Path(assets_dir) if assets_dir else ROOT / "assets"
        self._config = self._config_repo.load()
        # 聊天记录与配置同目录（测试注入 tmp_path 时不污染真实用户目录）
        self._chat_log_repo = chat_log_repo or ChatLogRepository(
            self._config_repo.path.parent)
        self._chat_history = ChatHistory(self._chat_log_repo)

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
            bounds_provider=lambda: self._screen.bounds_for(PET_SIZE, PET_SIZE))
        self._session = ChatSession(max_turns=self._config.pet.max_turns)
        self._local = LocalResponder(context_provider=self._local_context,
                                     on_remind=self._schedule_remind_action)
        self._ai_client = OpenAICompatClient()
        self._chat = ChatService(
            gateway=self._ai_client, session=self._session, bus=self._bus,
            config_provider=lambda: self._config.ai,
            system_prompt_provider=self._system_prompt,
            local_responder=self._local)
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

        self._animator = FrameAnimator(FrameAssetLoader(self._assets_dir,
                                                        self._config.pet.skin),
                                       size=PET_SIZE, parent=self._app)
        self._window = PetWindow(self._controller, self._animator,
                                 lambda: self._config, self._screen, size=PET_SIZE)
        self._bubble = SpeechBubble(
            self._screen, duration_provider=lambda: self._config.bubble.duration_s)
        self._chat_input = ChatInput(self._screen,                # 点宠物：正常聊天（单行输入条）
                                     idle_hide_provider=lambda: self._config.chat.idle_hide_s)
        self._chat_window = ChatWindow(                # 右键：可继续聊的历史窗
            self._screen,
            days_provider=self._chat_history.days,
            records_provider=self._chat_history.records_for)
        self._menu = PetMenu(on_settings=self.open_settings,
                             on_feed=self._on_feed,
                             on_pet_head=self._on_pet_head,
                             on_toggle_visible=self.toggle_visible,
                             on_quit=self._app.quit,
                             on_reload=self.reload_assets,
                             on_history=self._show_history,
                             on_focus=lambda: self._start_focus(
                                 self._config.focus.default_min),
                             on_stats=self._show_stats,
                             on_screenshot=self._screenshot_ask)
        self._stats_dialog = StatsDialog(self._screen)
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

        self._tick_timer = None          # start() 后才挂，测试可只构建不启动
        self._motion_timer = None        # 同上：只在需要位移推进时短时开启
        self._wire_ui()
        self._wire_events()

    # ---------- 生命周期 ----------

    def start(self) -> None:
        """素材检查 → 动画/工作线程/计时器启动 → 窗口显示（PRD 冷启动<3s）。"""
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
        self._hotkey = GlobalHotKey(
            callback=lambda: self._bus.publish(HotkeyChatRequestedEvent()))
        self._hotkey.start()
        self._sync_model_combo()
        self._sync_window()
        self._window.show()
        self._window.raise_()

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

    def shutdown(self) -> None:
        """退出前取消在途请求并落盘状态（PRD US-4 离线恢复）。"""
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
        if pet.lift_mood_to(60.0):
            # 积极的宠物：旧快照心情衰减贴底会让猫一开机就拉着脸（用户反馈），
            # 启动时把心情托底到 60，回来看到的是元气满满的那只
            logger.info("mood lifted to 60 on restore (was low)")
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
        return base + now_line + ACTION_PROMPT_GUIDE

    def _brain_source(self) -> str:
        """当前由谁作答：与 ChatService._brain_source 判据一致（只看 api_key）。"""
        return "ai" if self._config.ai.configured else "local"

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
        self._chat_input.set_history_count(len(self._chat_history.today()))
        logger.info("chat session restored turns=%d kept=%d max_turns=%d",
                    restored, len(self._session.turns), self._session.max_turns)

    def _log_chat_brain(self, when: str) -> None:
        """把“这条回复由谁作答”说清楚（PRD v1.6 C8）。

        本地小脑的回复也是逐段上屏，看气泡分不出来源，所以启动与改配置时各提一行。
        """
        ai = self._config.ai
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
            lambda text: self.send_message(text, self._chat_window.take_attachments()))
        self._chat_window.model_selected.connect(self._apply_model_profile)
        self._chat_input.model_selected.connect(self._apply_model_profile)
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
        self._router.on(ChatErrorEvent, self._on_chat_error)
        self._router.on(ReminderDueEvent, self._on_reminder)
        self._router.on(SoftJumpRequestedEvent, self._on_soft_jump)
        self._router.on(HotkeyChatRequestedEvent,
                        lambda _e: self._show_chat_input())
        self._router.on(ChatTurnRecordedEvent, self._on_chat_turn)
        self._router.on(ChatStateChangedEvent, self._on_chat_state)

    # ---------- 会话记录与状态 ----------

    def _on_chat_turn(self, event: ChatTurnRecordedEvent) -> None:
        """一条完整对话：按天落盘 + 追加到聊天窗历史区。"""
        record = self._chat_history.record(event)
        if record is not None:
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

    def _follow_chat_uis(self) -> None:
        """宠物挪窝（拖拽/走动/重力）后，可见的聊天界面重新侧向贴边（FR v1.10 C18）。

        拖拽小猫时历史窗无视「手动钉住」强制跟随：对话框要贴着猫走。
        """
        rect = self._window.window_rect()
        dragging = self._controller.pet.state is BehaviorState.DRAGGING
        self._chat_input.follow_pet(rect)
        self._chat_window.follow_pet(rect, force=dragging)

    def send_message(self, text: str, attachments=()) -> None:
        """入队成功才进入思考 → 预备流式气泡（空白消息不改状态，PRD M4 边界）。

        一句话定时提醒（「5分钟后提醒我开会」）走本地快速通道：正则命中直接排期，
        不等模型——快、省 token，也不受模型不守动作协议的影响（真机实测踩过）。
        带附件时跳过快速通道：图片/文件要走模型（视觉/上下文）。
        """
        attachments = tuple(attachments or ())
        if not str(text or "").strip() and not attachments:
            logger.debug("blank message dropped")
            return
        if not attachments:
            # 快速通道：专注番茄钟 / 取消提醒 / 每日提醒 / 一次性提醒，均不进 AI
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
        if not self._chat.send(text, attachments):
            logger.debug("blank message dropped")
            return
        self._growth.on_chat()   # 真实聊天消息计入成长积分
        self._controller.start_thinking()
        self._sync_window()
        self._streaming = True
        self._bubble.begin_stream(self._window.bubble_anchor())

    def _handle_quick_reminder(self, text: str, action: RemindAction) -> None:
        """本地快速通道：立即排期 + 双向落聊天记录 + 气泡确认。"""
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
        """抚摸：提升心情 + 成长积分。"""
        self._controller.pet_head()
        self._growth.on_pet()

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
        title = event.title or "WisePet"
        msec = int(max(3.0, float(self._config.bubble.duration_s)) * 1000)
        return self._tray.show_notification(title, event.text, msec=msec)

    def _on_chunk(self, event: ChatReplyChunkEvent) -> None:
        if event.done:
            self._streaming = False
            action = parse_action(event.reply)
            if action is not None:
                self._handle_action(action)   # 模型返回的是操作指令，不当文本展示
                return
            self._controller.start_speaking()
            self._tts.announce(event.reply)
            self._bubble.end_stream(event.reply, anchor=self._window.bubble_anchor(),
                                    on_close=self._controller.on_action_finished)
            self._sync_window()
            return
        if event.text_delta:
            self._bubble.append_chunk(event.text_delta)

    # ---------- 工具执行层：提醒 / 写文件 / PPT ----------

    def _schedule_remind_action(self, action: RemindAction) -> None:
        """本地小脑解析出的定时提醒（worker 线程回调；只发布事件，不碰 ui）。"""
        self._one_shot.schedule(action)
        logger.info("one-shot reminder scheduled: %s", describe_action(action))

    def _handle_action(self, action) -> None:
        """执行模型返回的操作意图：提醒直接排期；文件/PPT 先弹窗确认再写盘。"""
        anchor = self._window.bubble_anchor()
        self._controller.start_speaking()
        if isinstance(action, RemindAction):
            self._one_shot.schedule(action)
            self._bubble.end_stream(f"好，{describe_action(action)}，到点我叫你",
                                    anchor=anchor,
                                    on_close=self._controller.on_action_finished)
            self._sync_window()
            return
        self._bubble.end_stream(f"稍等，我确认一下：{describe_action(action)}",
                                anchor=anchor,
                                on_close=self._controller.on_action_finished)
        self._sync_window()
        if not self._confirm_action(action):
            self._controller.speak("好的，先不动文件。", source="system")
            return
        try:
            saved = (self._write_file(action)
                     if isinstance(action, CreateFileAction)
                     else build_ppt(action.path, action.title,
                                    [dict(slide) for slide in action.slides]))
            self._controller.speak(f"搞定啦，文件在这里：{saved}", source="system")
        except OSError as exc:
            logger.warning("action write failed: %s", exc)
            self._controller.speak(f"呜，没写成（{exc}）", source="system")

    def _write_file(self, action: CreateFileAction):
        path = Path(action.path).expanduser()
        if path.suffix == "":
            path = path.with_suffix(".txt")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(action.content, encoding="utf-8")
        logger.info("file written: %s (%d chars)", path, len(action.content))
        return path

    def _confirm_action(self, action) -> bool:
        """写盘前的用户确认：模型给的路径与内容必须过目（安全闸门）。"""
        if isinstance(action, CreateFileAction):
            title, headline = "WisePet 想创建文件", f"创建文件：\n{action.path}"
            detail = action.content or "（空文件）"
        else:
            title, headline = "WisePet 想生成 PPT", f"生成 PPT：\n{action.path}"
            detail = "\n".join(
                f"第{i}页 {slide.get('title') or '未命名'}："
                + "；".join(slide.get('bullets') or ())
                for i, slide in enumerate(action.slides, 1))
        box = QMessageBox(self._window)
        box.setWindowTitle(title)
        box.setText(headline)
        box.setDetailedText(detail[:4000])
        yes = box.addButton("执行", QMessageBox.AcceptRole)
        box.addButton("先不写", QMessageBox.RejectRole)
        box.exec()
        return box.clickedButton() is yes

    def _generate_cheer(self) -> str | None:
        """鼓励语生成：AI 在线就出一句古诗名句/夸奖；未配置或失败返回 None（走本地语录）。"""
        ai = self._config.ai
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

    def _summarize_news(self, headlines: list[str]) -> str | None:
        """新闻标题 → AI 摘要；未配 Key 或失败时返回 None（走纯标题播报）。"""
        ai = self._config.ai
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
        ai = self._config.ai
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
        self._set_chat_state(PHASE_ERROR, detail=event.kind)
        self._controller.speak(ChatService.fallback_text(event.kind), source="fallback")

    def _on_reminder(self, event: ReminderDueEvent) -> None:
        self._reminder_kind = event.kind
        cursor = QCursor.pos()
        self._controller.move_to(cursor.x() - PET_SIZE // 2, cursor.y() - PET_SIZE // 2)
        title = "喝水提醒" if event.kind == KIND_WATER else "久坐提醒"
        self._announce_reminder(event.text, title=title)

    def _announce_reminder(self, text: str, title: str = "提醒") -> None:
        """提醒播报：配了 AI 就后台润色成人设口吻；失败/未配置退回原文（必响）。"""
        ai = self._config.ai
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
        ai = self._config.ai
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
        if self._reminder_kind is not None:
            self._reminder.notify_ack(self._reminder_kind)
            self._reminder_kind = None
        self._controller.on_action_finished()

    def open_settings(self) -> None:
        dialog = SettingsDialog(self._config, on_saved=self.apply_config,
                                skin_options=self._available_skins(),
                                screen_bounds=self._screen,
                                parent=self._window)
        dialog.exec()

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
        logger.info("config applied: model=%s skin=%s walk=%.0fpx/s",
                    config.ai.model, config.pet.skin, config.pet.walk_speed_px_per_s)
        self._sync_model_combo()
        self._log_chat_brain(when="config-saved")

    # ---------- 多模型方案 ----------

    def _sync_model_combo(self) -> None:
        """聊天窗/输入条下拉 = 方案列表；选中项 = 当前生效配置。"""
        names = [p.name for p in self._config.ai_profiles]
        active = self._active_profile_name()
        self._chat_window.set_models(names, active)
        self._chat_input.set_models(names, active)

    def _active_profile_name(self) -> str:
        ai = self._config.ai
        for profile in self._config.ai_profiles:
            if (profile.base_url == ai.base_url and profile.model == ai.model
                    and profile.api_key == ai.api_key):
                return profile.name
        return names[0] if (names := [p.name for p in self._config.ai_profiles]) else ""

    def _apply_model_profile(self, name: str) -> None:
        """聊天窗切模型：只换 base_url/key/model 三件套，温度超时等保持不动。"""
        profile = next((p for p in self._config.ai_profiles if p.name == name), None)
        if profile is None:
            logger.debug("model profile not found: %s", name)
            return
        ai = replace(self._config.ai, base_url=profile.base_url,
                     api_key=profile.api_key, model=profile.model)
        if ai == self._config.ai:
            return
        logger.info("model switched via chat window: %s (%s)", name, profile.model)
        self.apply_config(replace(self._config, ai=ai))

    def toggle_visible(self) -> None:
        visible = self._window.isVisible()
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

    def _sync_window(self) -> None:
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


def main() -> int:
    """入口：单实例互斥 → 日志 → 装配 → 启动 → 事件循环。"""
    started_at = time.monotonic()
    app = QApplication(sys.argv)
    app.setApplicationName("WisePet")

    global _instance_guard
    _instance_guard = InstanceGuard()
    if not _instance_guard.acquire():
        QMessageBox.warning(None, "WisePet", "已经有一个桌宠在跑啦，不再重复启动。")
        return 1

    migrated = migrate_legacy_data()
    log_file = setup_logging()
    logger.info("bootstrapping (log=%s)", log_file)
    if migrated:
        logger.info("legacy data migrated from ~/.ai-bot: %s", migrated)

    pet_app = PetApplication(app)
    app.aboutToQuit.connect(pet_app.shutdown)
    pet_app.start()
    logger.info("cold start took %.2fs (target <3s)", time.monotonic() - started_at)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
