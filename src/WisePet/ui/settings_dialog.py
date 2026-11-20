"""设置窗口：AI 配置 / 人设 / 宠物参数 / 提醒（PRD M7 / US-6 / FR v1.12 C22 / v1.13 C23）。

api_key 安全约定（AGENT.md 规约 5）：输入框永不明文回显已存值，
只用 mask_key 作为 placeholder 提示；保存时留空即视为「不修改」。
外观与聊天输入条 / 历史窗同一套贴纸风：无边框圆角卡片、自定义标题栏、
分区小卡片与统一 QSS，不再用系统 GroupBox + 系统按钮的裸控件观感。
"""
from __future__ import annotations

import logging
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEvent, QPoint, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QPolygon
from PySide6.QtWidgets import (
    QApplication,
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..app.chat_service import ChatService
from ..domain.config_model import (
    DEFAULT_SKIN,
    PET_SKINS,
    AIProfile,
    AppConfig,
    BubbleSettings,
    PersonaConfig,
    ReminderSettings,
    LocationSettings,
    WebSearchSettings,
    WeatherSettings,
    normalize_purpose,
    normalize_skin,
)
from ..domain.errors import AIGatewayError
from ..domain.ports import PURPOSES
from ..infra.config_repo import mask_key
from ..infra.openai_client import probe
from .screen_bounds import ScreenBounds

logger = logging.getLogger(__name__)

#: 表单取值范围（PRD M6 边界：间隔下限 5min；PRD M1：速度极值不越界）
_TEMP_MAX = 2.0
_TIMEOUT_MIN_S, _TIMEOUT_MAX_S = 1, 120
_WALK_SPEED_MAX = 400.0
_IDLE_GAP_MIN_S, _IDLE_GAP_MAX_S = 1, 120
_MAX_TURNS_MIN, _MAX_TURNS_MAX = 1, 100
_INTERVAL_MAX_MIN = 600

_HINT_KEEP_KEY = "留空表示不修改（当前 %s）"
_NO_KEY_HINT = "留空也能聊（走本地小脑），填上 Key 接 AI 大脑会更聪明"

#: 皮肤下拉的展示名（中文标签属 ui 关注点，id 来自 domain.PET_SKINS）
SKIN_LABELS = {"cat": "橘猫", "dog": "柴犬", "monkey": "小猴"}

#: 与 ChatInput / ChatWindow / SpeechBubble 同源的贴纸风配色
_STYLE = """
QFrame#settingsCard {
    background: #FFFDF7;
    border: 1px solid #DFC49A;
    border-radius: 16px;
}
QFrame#titleBar { background: transparent; }
QWidget#settingsContent, QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 0 6px 0 8px; }
QScrollBar::handle:vertical {
    background: #E2CFAF; border-radius: 4px; min-height: 24px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QLabel { color: #6B4F3A; font-size: 11px; }
QLabel#title { color: #5A3E28; font-size: 15px; font-weight: 600; }
QLabel#dot { color: #E8A45C; font-size: 16px; }
QLabel#sectionTitle { color: #6B4F3A; font-size: 12px; font-weight: 600; }
QLabel#sectionDot { color: #E8A45C; font-size: 12px; }
QFrame#section {
    background: #FFFBF2;
    border: 1px solid #EFE0C6;
    border-radius: 12px;
}
QPushButton#close {
    background: transparent; border: none; color: #A08A6C;
    font-size: 18px; border-radius: 10px; min-width: 26px; min-height: 26px;
}
QPushButton#close:hover { background: #F3E1C6; color: #6B4F3A; }
QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox {
    background: #FFFFFF; border: 1px solid #E7D3B2; border-radius: 9px;
    padding: 6px 10px; color: #3A2B21; font-size: 12px;
    selection-background-color: #F3D9A8;
}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border: 1px solid #E8A45C;
}
QComboBox {
    background: #FFFFFF; border: 1px solid #E2CFAF; border-radius: 9px;
    padding: 6px 10px; color: #6B4F3A; font-size: 12px;
}
QComboBox::drop-down { border: 0; width: 16px; }
QComboBox QAbstractItemView {
    background: #FFFDF7; border: 1px solid #E2CFAF;
    selection-background-color: #F7E7CC; color: #3A2B21;
}
QCheckBox {
    color: #3A2B21; font-size: 11px; spacing: 6px; background: transparent;
}
QCheckBox::indicator {
    width: 14px; height: 14px; border: 1px solid #DFC49A;
    border-radius: 4px; background: #FFFFFF;
}
QCheckBox::indicator:checked { background: #F8D79B; border: 1px solid #E8A45C; }
QLabel#sectionNote { color: #A08A6C; font-size: 11px; }
/* 微调按钮改成自绘的贴纸风图标按钮（见 _StickerSteppers）。
   QSS 的 ::up-arrow border 三角形会被 Qt 渲染成深色实心方块，实在太丑，弃用。 */
QSpinBox, QDoubleSpinBox {
    padding-right: 28px;
    selection-background-color: #F8D79B; selection-color: #3A2B21;
}
/* 原生按钮必须留着参与布局，但视觉上藏起来，交给 _StickerSteppers 的子按钮 */
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {
    background: transparent; border: none;
}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow,
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
    width: 0; height: 0; border: none; image: none;
}
QToolButton#stepUp, QToolButton#stepDown {
    background: #FFF3DE; border: 1px solid #E8C89B; border-radius: 4px;
    padding: 0; margin: 0;
}
QToolButton#stepUp:hover, QToolButton#stepDown:hover {
    background: #F8D79B; border: 1px solid #E8A45C;
}
QToolButton#stepUp:pressed, QToolButton#stepDown:pressed {
    background: #E8A45C; border: 1px solid #D98C42;
}
QDialogButtonBox QPushButton {
    background: #FFFFFF; border: 1px solid #E2CFAF; border-radius: 9px;
    color: #6B4F3A; font-size: 12px; padding: 6px 16px;
}
QDialogButtonBox QPushButton:hover { background: #F7E7CC; }
QPushButton#saveBtn {
    background: #F8D79B; border: 1px solid #E7C79A; border-radius: 9px;
    color: #5A3E28; font-size: 12px; font-weight: 600; padding: 6px 16px;
}
QPushButton#saveBtn:hover { background: #F3D9A8; }
QPushButton#profileAdd, QPushButton#profileDel {
    background: #FFFDF7; border: 1px solid #E2CFAF; border-radius: 8px;
    color: #6B4F3A; font-size: 14px; padding: 0;
    min-width: 0; max-width: 28px; min-height: 0; max-height: 28px;
}
QPushButton#profileAdd:hover, QPushButton#profileDel:hover { background: #F7E7CC; }
"""

#: 状态胶囊：成功暖奶油、失败淡粉（与历史窗兜底气泡同色），不再用刺眼的纯红绿
_STATUS_OK = ("color:#6B4F3A; background:#F7EEDC; border:1px solid #E7C79A; "
              "border-radius:9px; padding:6px 10px; font-size:11px;")
_STATUS_ERROR = ("color:#8A4A4A; background:#F7E2E2; border:1px solid #E7C3C3; "
                 "border-radius:9px; padding:6px 10px; font-size:11px;")


def _forward_wheel(self, event) -> None:
    """滚轮交给设置窗的滚动区视口，而不是让数值/选项控件自己改值。

    只 `ignore()` 并不会被 QScrollArea 接住（实测页面不动），所以显式转发；
    之后 accept 阻止 Qt 再向父级冒泡，避免滚两格。
    """
    if self.wheel_target is not None:
        QApplication.sendEvent(self.wheel_target, event)
    event.accept()


@lru_cache(maxsize=2)
def _triangle_icon(up: bool) -> QIcon:
    """在内存里画三角箭头：QSS 的 border 三角形在 Qt 里会渲染成实心方块，零素材依赖。"""
    pixmap = QPixmap(12, 12)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#6B4F3A"))
    points = ((QPoint(6, 3), QPoint(10, 9), QPoint(2, 9)) if up
              else (QPoint(2, 3), QPoint(10, 3), QPoint(6, 9)))
    painter.drawPolygon(QPolygon(list(points)))
    painter.end()
    return QIcon(pixmap)


class _StickerSteppers:
    """数值框右侧两个贴纸风圆角按钮，替掉原生方块箭头。

    混在 QSpinBox / QDoubleSpinBox 前面组成 MRO；按钮是 spinbox 的子控件，
    滚轮事件仍会冒泡到 spinbox 的 _forward_wheel，页面照常滚。
    """

    def _init_steppers(self) -> None:
        # 必须保留原生 UpDownArrows：改成 NoButtons 后 Qt 会把 lineEdit 算成 1px 宽
        # （实测数值文字整个消失）。原生按钮由 QSS 透明化，视觉交给下面两个子按钮。
        self._step_up = QToolButton(self)
        self._step_down = QToolButton(self)
        for button, name, up in ((self._step_up, "stepUp", True),
                                (self._step_down, "stepDown", False)):
            button.setObjectName(name)
            button.setIcon(_triangle_icon(up))
            button.setIconSize(QSize(10, 10))
            button.setCursor(Qt.PointingHandCursor)
            button.setToolTip("增加 1" if up else "减少 1")
        self._step_up.clicked.connect(lambda _checked=False: self.stepBy(1))
        self._step_down.clicked.connect(lambda _checked=False: self.stepBy(-1))

    def resizeEvent(self, event):  # noqa: N802 - Qt 重写
        super().resizeEvent(event)
        if not hasattr(self, "_step_up"):
            return                       # 构造期 Qt 会先派发一次 resize
        width, height = self.width(), self.height()
        button_width, gap = 20, 3
        x = width - button_width - gap
        half = (height - 2 * gap - 1) // 2
        self._step_up.setGeometry(x, gap, button_width, half)
        self._step_down.setGeometry(x, gap + half + 1, button_width,
                                    height - gap - half - 1)


class _NoWheelSpinBox(_StickerSteppers, QSpinBox):
    wheel_target: QWidget | None = None
    wheelEvent = _forward_wheel                     # noqa: N815 - Qt 重写

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._init_steppers()


class _NoWheelDoubleSpinBox(_StickerSteppers, QDoubleSpinBox):
    wheel_target: QWidget | None = None
    wheelEvent = _forward_wheel                     # noqa: N815 - Qt 重写

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._init_steppers()


class _NoWheelComboBox(QComboBox):
    wheel_target: QWidget | None = None
    wheelEvent = _forward_wheel                     # noqa: N815 - Qt 重写


class SettingsDialog(QDialog):
    """模态设置窗；保存成功经 `on_saved` 回调交回组装根落盘。"""

    def __init__(self, config: AppConfig,
                 on_saved: Callable[[AppConfig], None] | None = None,
                 skin_options: list[str] | None = None,
                 screen_bounds: ScreenBounds | None = None,
                 parent: QWidget | None = None,
                 is_manager: bool = True,
                 profile_name: str = "",
                 on_change_pin: Callable[[], None] | None = None,
                 current_data_dir: str = "",
                 on_data_dir_change: Callable[[str], str] | None = None):
        """`skin_options=None` 表示展示全部已定义皮肤；传入则只展示素材齐全的皮肤，
        但当前已选皮肤永远保留（避免静默把用户的选项改掉）。
        is_manager：当前是否管理员（默认身份）——家庭共享 AI 只有管理员可配。"""
        super().__init__(parent)
        self._base = config
        self._on_saved = on_saved
        self._screen = screen_bounds
        self._is_manager = is_manager
        self._identity_name = profile_name
        self._on_change_pin = on_change_pin
        self._current_data_dir = str(current_data_dir or "")
        self._on_data_dir_change = on_data_dir_change
        self._skins = list(skin_options) if skin_options else list(PET_SKINS)
        if config.pet.skin not in self._skins:
            self._skins.append(normalize_skin(config.pet.skin))
        self._drag_global: QPoint | None = None
        self._drag_window_pos: QPoint | None = None

        self.setWindowTitle("WisePet 设置")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(560, 460)
        self.resize(640, 600)
        self.setStyleSheet(_STYLE)

        card = QFrame(self)
        card.setObjectName("settingsCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

        # 整条标题栏是一个容器 widget：空白处、圆点、文字都能拖动，
        # 关闭按钮不装过滤器，点击仍只走 reject（FR v1.13 C23）。
        self._title_bar = QFrame(card)
        self._title_bar.setObjectName("titleBar")
        self._title_bar.setMouseTracking(True)
        dot = QLabel("●", self._title_bar)
        dot.setObjectName("dot")
        title = QLabel("设置", self._title_bar)
        title.setObjectName("title")
        self._close_btn = QPushButton("×", self._title_bar)
        self._close_btn.setObjectName("close")
        self._close_btn.setToolTip("关闭")
        self._close_btn.clicked.connect(self.reject)
        head = QHBoxLayout(self._title_bar)
        head.setContentsMargins(16, 14, 12, 6)
        head.setSpacing(8)
        head.addWidget(dot)
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self._close_btn)

        self._scroll = QScrollArea(card)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        # 横向滚动条一出，右侧留白就被吃掉（真机截图踩过）：宽度不够就压字段
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("settingsContent")
        body = QVBoxLayout(content)
        # 右侧 24px：滚动条（占位 14px）悬在留白带里，不再压住分区卡片
        body.setContentsMargins(16, 4, 24, 8)
        body.setSpacing(10)
        body.addWidget(self._build_ai_group(content))
        body.addWidget(self._build_persona_group(content))
        body.addWidget(self._build_pet_group(content))
        body.addWidget(self._build_reminder_group(content))
        body.addWidget(self._build_companion_group(content))
        body.addWidget(self._build_experience_group(content))
        body.addWidget(self._build_storage_group(content))
        body.addWidget(self._build_system_group(content))
        body.addStretch(1)
        self._scroll.setWidget(content)

        self._status = QLabel("", card)
        self._status.setWordWrap(True)
        self._status.hide()                # 没结果时不占一条空的米色横条

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel,
                                   Qt.Horizontal, card)
        buttons.button(QDialogButtonBox.Save).setObjectName("saveBtn")
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        self._test_btn = QPushButton("测试连接", card)
        self._test_btn.setObjectName("testBtn")
        self._test_btn.clicked.connect(self._test_connection)
        buttons.addButton(self._test_btn, QDialogButtonBox.ActionRole)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(card)
        root.setContentsMargins(0, 0, 0, 12)
        root.setSpacing(0)
        root.addWidget(self._title_bar)
        root.addWidget(self._scroll, 1)
        status_row = QHBoxLayout()
        status_row.setContentsMargins(16, 6, 16, 4)
        status_row.addWidget(self._status, 1)
        root.addLayout(status_row)
        button_row = QHBoxLayout()
        button_row.setContentsMargins(16, 4, 16, 0)
        button_row.addWidget(buttons)
        root.addLayout(button_row)

        # 构造末尾才装过滤器：Qt 装完可能立刻派发 polish 事件
        for widget in (card, self._title_bar, dot, title):
            widget.installEventFilter(self)

        # 滚轮停在数值框 / 下拉上时只滚页面，不偷偷改值（FR v1.14 C24）
        value_widgets = (self._temperature, self._timeout, self._walk_speed,
                         self._idle_min, self._idle_max, self._max_turns,
                         self._chat_idle_hide,
                         self._tts_volume, self._tts_rate, self._weekly_hour,
                         self._water_interval, self._sedentary_interval,
                         self._quiet_daily, self._quiet_per_source,
                         self._memory_search_limit,
                         self._log_max_mb, self._log_retention,
                         self._trickle_every, self._reunion_hours,
                         self._roam_jump_interval, self._focus_default,
                         self._bubble_duration, self._dnd_start, self._dnd_end,
                         self._cheer_interval, self._news_interval,
                         self._max_recalls, self._task_poll,
                         self._knowledge_top_k, self._event_limit)
        for widget in value_widgets + (self._skin, self._weekly_weekday,
                                       self._action_permission, self._log_level,
                                       self._activity, self._overflow_mode,
                                       self._enter_effect):
            widget.wheel_target = self._scroll.viewport()
        for widget in value_widgets:
            # 上一轮把滚轮改成「只滚页面」、微调按钮又做成透明，用户以为没法输入。
            # 提示里明确「点框直接键入」，并保留键盘/滚轮口径给既有回归闸。
            widget.setToolTip("点击本框直接键入数字（键盘输入）；右侧 ▲▼ 可微调；滚轮只滚页面")

        # 有 parent 的 QDialog 默认居中于右下角的宠物窗，可能一开始就落在
        # 工作区外；显式摆到屏幕上方中间，拖动时才不会被 clamp 拽回去。
        if self._screen is not None:
            x1, y1, x2, y2 = self._screen.geometry()
            center_x = x1 + (x2 - x1 - self.width()) // 2
            self.move(*self._screen.clamp(center_x, y1 + 40,
                                          self.width(), self.height()))

        self._load_from(self._base)

    # ---------- 表单构建 ----------

    @staticmethod
    def _section(parent: QWidget, title: str,
                 actions: list | None = None) -> tuple[QFrame, QFormLayout]:
        """一张分区小卡片：橘点 + 标题（右侧可挂小按钮）+ 表单，替代 QGroupBox。"""
        frame = QFrame(parent)
        frame.setObjectName("section")
        dot = QLabel("●", frame)
        dot.setObjectName("sectionDot")
        label = QLabel(title, frame)
        label.setObjectName("sectionTitle")
        heading = QHBoxLayout()
        heading.setSpacing(6)
        heading.addWidget(dot)
        heading.addWidget(label)
        heading.addStretch(1)
        for widget in actions or []:
            heading.addWidget(widget, 0, Qt.AlignVCenter)
        form = QFormLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(6)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        body = QVBoxLayout(frame)
        body.setContentsMargins(12, 10, 12, 12)
        body.setSpacing(8)
        body.addLayout(heading)
        body.addLayout(form)
        return frame, form

    def _build_ai_group(self, parent: QWidget) -> QFrame:
        # ＋/－ 放在分区标题行右侧：字段列宽与其它行完全一致，不再挤压/溢出
        self._profile_combo = _NoWheelComboBox(parent)
        self._profile_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._profile_combo.setMinimumContentsLength(4)
        self._profile_combo.setMinimumWidth(60)
        self._profile_combo.currentTextChanged.connect(self._on_profile_selected)
        self._profile_add = QPushButton("＋", parent)
        self._profile_add.setObjectName("profileAdd")
        self._profile_add.setFixedSize(26, 26)
        self._profile_add.setToolTip("新增一个模型方案")
        self._profile_add.clicked.connect(self._add_profile)
        self._profile_del = QPushButton("－", parent)
        self._profile_del.setObjectName("profileDel")
        self._profile_del.setFixedSize(26, 26)
        self._profile_del.setToolTip("删除当前选中的方案（至少保留一个）")
        self._profile_del.clicked.connect(self._remove_profile)
        box, form = self._section(parent, "AI 服务（OpenAI 兼容）",
                                  actions=[self._profile_add, self._profile_del])
        self._profile_combo.setParent(box)
        self._profile_add.setParent(box)
        self._profile_del.setParent(box)
        self._profile_name = QLineEdit(box)
        self._profile_name.setPlaceholderText("方案名（聊天窗下拉里显示）")
        self._purpose_combo = _NoWheelComboBox(box)
        self._purpose_combo.addItems(["chat · 对话", "image · 画图", "video · 视频"])
        self._purpose_combo.setToolTip(
            "这个方案用来做什么：chat 用于聊天，image 用于画图，video 用于生成视频。"
            "说「画一张…」时会自动调用 image 方案。")
        self._base_url = QLineEdit(box)
        self._api_key = QLineEdit(box)
        self._api_key.setEchoMode(QLineEdit.Password)   # 不回显、不日志
        self._model = QLineEdit(box)
        self._temperature = _NoWheelDoubleSpinBox(box)
        self._temperature.setRange(0.0, _TEMP_MAX)
        self._temperature.setSingleStep(0.1)
        self._timeout = _int_spin(_TIMEOUT_MIN_S, _TIMEOUT_MAX_S, 30, " s", box)
        form.addRow("模型方案", self._profile_combo)
        form.addRow("方案名", self._profile_name)
        form.addRow("用途", self._purpose_combo)
        form.addRow("Base URL", self._base_url)
        form.addRow("API Key", self._api_key)
        form.addRow("Model", self._model)
        self._embedding_model = QLineEdit(box)
        self._embedding_model.setPlaceholderText("语义检索用的 embedding 模型（留空禁用）")
        form.addRow("Embedding 模型", self._embedding_model)
        identity_label = QLabel(
            f"当前身份：{self._identity_name or '默认身份'} · "
            f"{'👑 管理员（可共享 AI 给全家）' if self._is_manager else '普通成员'}", box)
        identity_label.setObjectName("pickerHint")
        form.addRow(" ", identity_label)
        self._share_family = QCheckBox("共享给全家人（家人勾「使用家庭共享 AI」即共用我的 Key）",
                                       box)
        self._share_family.setToolTip("只有管理员（默认身份）可以共享；"
                                      "Key 保存在本机全局文件，不会写进家人配置")
        if not self._is_manager:
            self._share_family.setEnabled(False)
            self._share_family.setToolTip(
                f"当前身份是「{self._identity_name or '默认身份'}」，不是管理员。"
                "切回默认身份后才能共享 AI 给全家")
        form.addRow("家庭共享", self._share_family)
        self._change_pin_btn = QPushButton("🔑 修改我的 PIN…", box)
        self._change_pin_btn.setObjectName("pickerAction")
        if self._on_change_pin is not None:
            self._change_pin_btn.clicked.connect(lambda: self._on_change_pin())
        else:
            self._change_pin_btn.setEnabled(False)
        form.addRow("我的 PIN", self._change_pin_btn)
        form.addRow("Temperature", self._temperature)
        form.addRow("超时", self._timeout)
        return box

    # ---------- 模型方案编辑（列表在聊天窗下拉里切换） ----------

    def _on_profile_selected(self, _name: str) -> None:
        """下拉换方案：先把未保存改动写回旧方案，再载入新方案并设为生效方案。

        设置窗这个下拉不只是「编辑谁」——选中的方案就是保存后 cfg.ai 生效的那份，
        否则用户新加一个方案填好模型、选中保存，实际调用的还是旧模型。
        """
        if not hasattr(self, "_profiles"):
            return
        self._flush_form_to_profile()
        selected = self._profile_combo.currentText()
        self._active_profile_name = selected
        self._load_profile_fields(selected)

    def _load_profile_fields(self, name: str) -> None:
        profile = next((p for p in self._profiles if p.name == name), None)
        if profile is None:
            return
        self._form_profile_name = name
        self._profile_name.setText(profile.name)
        purpose = normalize_purpose(getattr(profile, "purpose", ""))
        self._purpose_combo.setCurrentIndex(PURPOSES.index(purpose))
        self._base_url.setText(profile.base_url)
        self._model.setText(profile.model)
        self._api_key.clear()                    # 敏感值不回填，与单方案时代同口径
        self._api_key.setPlaceholderText(
            _HINT_KEEP_KEY % mask_key(profile.api_key)
            if profile.api_key else _NO_KEY_HINT)

    def _flush_form_to_profile(self) -> list:
        """表单 → 当前编辑中的方案（含改方案名）；返回最新方案列表。"""
        editing = getattr(self, "_form_profile_name", "")
        if not editing:
            return self._profiles
        new_name = self._profile_name.text().strip() or editing
        for index, profile in enumerate(self._profiles):
            if profile.name != editing:
                continue
            purpose = PURPOSES[self._purpose_combo.currentIndex()] \
                if 0 <= self._purpose_combo.currentIndex() < len(PURPOSES) \
                else "chat"
            self._profiles[index] = replace(
                profile, name=new_name,
                base_url=self._base_url.text().strip() or profile.base_url,
                api_key=self._api_key.text().strip() or profile.api_key,
                model=self._model.text().strip() or profile.model,
                embedding_model=self._embedding_model.text().strip()
                or profile.embedding_model,
                purpose=purpose)
            break
        self._form_profile_name = new_name
        if getattr(self, "_active_profile_name", "") == editing:
            self._active_profile_name = new_name
        # 同步下拉项文字（改了名的情况），不触发切换
        self._profile_combo.blockSignals(True)
        index = self._profile_combo.findText(editing)
        if index >= 0:
            self._profile_combo.setItemText(index, new_name)
        self._profile_combo.blockSignals(False)
        return self._profiles

    def _add_profile(self) -> None:
        self._flush_form_to_profile()
        existing = {p.name for p in self._profiles}
        index = 1
        while f"方案{index}" in existing:
            index += 1
        self._profiles.append(AIProfile(name=f"方案{index}"))
        self._profile_combo.blockSignals(True)
        self._profile_combo.addItem(f"方案{index}")
        self._profile_combo.blockSignals(False)
        self._profile_combo.setCurrentText(f"方案{index}")
        self._load_profile_fields(f"方案{index}")

    def _remove_profile(self) -> None:
        if len(self._profiles) <= 1:
            self._show_status("至少要保留一个模型方案", ok=False)
            return
        name = self._form_profile_name
        self._profiles = [p for p in self._profiles if p.name != name]
        self._profile_combo.blockSignals(True)
        index = self._profile_combo.findText(name)
        if index >= 0:
            self._profile_combo.removeItem(index)
        self._profile_combo.blockSignals(False)
        self._form_profile_name = ""
        self._load_profile_fields(self._profile_combo.currentText())

    def _build_persona_group(self, parent: QWidget) -> QFrame:
        box, form = self._section(parent, "人设")
        self._persona_name = QLineEdit(box)
        self._system_prompt = QPlainTextEdit(box)
        self._system_prompt.setFixedHeight(90)
        self._system_prompt.setPlaceholderText("可用占位符：{name}、{vitals_summary}")
        self._persona_anchor = QCheckBox("固定说话风格（换模型也不跑偏）", box)
        self._persona_anchor.setToolTip("内置几段标准语气样例注入提示词，保持宠物性格稳定")
        form.addRow("名字", self._persona_name)
        form.addRow("System Prompt", self._system_prompt)
        form.addRow("性格锚点", self._persona_anchor)
        return box

    def _build_pet_group(self, parent: QWidget) -> QFrame:
        box, form = self._section(parent, "宠物参数")
        self._walk_speed = _NoWheelDoubleSpinBox(box)
        self._walk_speed.setRange(0.0, _WALK_SPEED_MAX)
        self._walk_speed.setSuffix(" px/s")
        self._idle_min = _int_spin(_IDLE_GAP_MIN_S, _IDLE_GAP_MAX_S, 5, " s", box)
        self._idle_max = _int_spin(_IDLE_GAP_MIN_S, _IDLE_GAP_MAX_S, 15, " s", box)
        self._max_turns = _int_spin(_MAX_TURNS_MIN, _MAX_TURNS_MAX, 20, " 轮", box)
        self._skin = _NoWheelComboBox(box)
        for skin in self._skins:
            self._skin.addItem(SKIN_LABELS.get(skin, skin), skin)
        self._activity = _NoWheelComboBox(box)
        for label, value in (("安静一点", "quiet"), ("正常（推荐）", "normal"),
                             ("活泼一点", "lively")):
            self._activity.addItem(label, value)
        self._activity.setToolTip("只影响多久自己出发一次，不改状态机与全部状态轮播")
        self._enter_effect = _NoWheelComboBox(box)
        for label, value in (("自动（按场景切换）", "auto"),
                             ("随机（每次不同）", "random"),
                             ("粒子汇聚（开机）", "spawn"),
                             ("粒子爆开（庆祝）", "burst"),
                             ("碎片拼合（几何）", "puzzle"),
                             ("HUD 扫描（极客）", "terminal"),
                             ("能量揭幕（换场）", "curtain"),
                             ("能量波纹（优雅）", "ink"),
                             ("聚焦光束（寻回）", "magnifier")):
            self._enter_effect.addItem(label, value)
        self._enter_effect.setToolTip("宠物登场时播放的特效；7 种风格共享同一套汇聚环/扫描环/白核爆闪骨架。选「自动」按场景切换，其中「从托盘恢复」每次随机；选「随机」则每次都随机")
        self._stick_to_floor = QCheckBox(
            "松手后自动落回屏幕底部（取消勾选可停在任意位置）", box)
        self._roam = QCheckBox(
            "自由漫游：可停留在桌面任意高度走动/睡觉，偶尔柔和地瞬移换地方", box)
        form.addRow("形象", self._skin)
        form.addRow("活泼度", self._activity)
        form.addRow("进入特效", self._enter_effect)
        form.addRow("走动速度", self._walk_speed)
        form.addRow("闲置间隔下限", self._idle_min)
        form.addRow("闲置间隔上限", self._idle_max)
        form.addRow("历史保留", self._max_turns)
        self._chat_idle_hide = _int_spin(0, 600, 30, " s", box)
        self._chat_idle_hide.setToolTip("聊天输入条多久没交互就自动收起；0 = 不自动隐藏")
        form.addRow("聊天框空闲隐藏", self._chat_idle_hide)
        form.addRow("贴地", self._stick_to_floor)
        form.addRow("漫游", self._roam)
        self._roam_jump_interval = _int_spin(0, 600, 30, " s", box)
        self._roam_jump_interval.setToolTip("每隔多久柔和瞬移换一次地方；0 = 不瞬移")
        form.addRow("瞬移间隔", self._roam_jump_interval)
        self._news_enabled = QCheckBox("启用（RSS 拉标题，配了 AI 时播报摘要）", box)
        self._news_interval = _int_spin(1, 1440, 60, " min", box)
        form.addRow("新闻播报", self._news_enabled)
        form.addRow("新闻间隔", self._news_interval)
        self._cheer_enabled = QCheckBox("启用（积极的话 / 古诗名句 / 夸奖）", box)
        self._cheer_interval = _int_spin(1, 1440, 3, " min", box)
        form.addRow("鼓励推送", self._cheer_enabled)
        form.addRow("鼓励间隔", self._cheer_interval)
        self._dnd_enabled = QCheckBox("启用（时段内喝水/久坐/新闻/鼓励顺延到时段结束）", box)
        self._dnd_start = _int_spin(0, 23, 12, " 点", box)
        self._dnd_end = _int_spin(0, 23, 14, " 点", box)
        form.addRow("勿扰时段", self._dnd_enabled)
        start_end = QHBoxLayout()
        start_end.setSpacing(6)
        start_end.addWidget(QLabel("从"))
        start_end.addWidget(self._dnd_start)
        start_end.addWidget(QLabel("点到"))
        start_end.addWidget(self._dnd_end)
        start_end.addWidget(QLabel("点"))
        start_end.addStretch(1)
        holder = QWidget(box)
        holder.setLayout(start_end)
        form.addRow("", holder)
        self._focus_default = _int_spin(1, 180, 25, " min", box)
        self._focus_default.setToolTip("说「来个番茄钟」或右键菜单时的默认专注时长")
        form.addRow("专注时长", self._focus_default)
        return box

    def _build_reminder_group(self, parent: QWidget) -> QFrame:
        box, form = self._section(parent, "提醒陪伴")
        minimum = int(self._base.reminders.min_interval_min)
        self._bubble_duration = _int_spin(3, 120, 15, " s", box)
        self._bubble_duration.setToolTip("所有通知气泡的停留时长（未点「知道啦」时自动消失）")
        form.addRow("弹窗停留", self._bubble_duration)
        self._water_enabled = QCheckBox("启用", box)
        self._water_interval = _int_spin(minimum, _INTERVAL_MAX_MIN, 45, " min", box)
        self._sedentary_enabled = QCheckBox("启用", box)
        self._sedentary_interval = _int_spin(minimum, _INTERVAL_MAX_MIN, 60, " min", box)
        for check in (self._water_enabled, self._sedentary_enabled):
            check.toggled.connect(lambda _on: self._sync_reminder_labels())
        form.addRow("喝水", self._water_enabled)
        form.addRow("喝水间隔", self._water_interval)
        form.addRow("久坐", self._sedentary_enabled)
        form.addRow("久坐间隔", self._sedentary_interval)
        return box

    def _build_companion_group(self, parent: QWidget) -> QFrame:
        """v2.2 陪伴进阶：语音说话 / 系统通知中心 / 每周回忆录 / 成长体系。"""
        box, form = self._section(parent, "陪伴进阶")
        self._tts_enabled = QCheckBox("语音说话（本地 TTS，提醒/回复可开口朗读）", box)
        self._tts_volume = _int_spin(0, 100, 100, " %", box)
        self._tts_volume.setToolTip("朗读音量 0~100（Windows 生效；macOS 走系统音量）")
        self._tts_rate = _int_spin(-10, 10, 0, "", box)
        self._tts_rate.setToolTip("语速 -10（慢）~ 10（快），0 为正常")
        form.addRow("语音说话", self._tts_enabled)
        form.addRow("朗读音量", self._tts_volume)
        form.addRow("语速", self._tts_rate)
        self._notify_system = QCheckBox(
            "通知走系统通知中心（关闭则用宠物头顶气泡）", box)
        self._notify_system.setToolTip(
            "开启后提醒等通知进 Windows 动作中心 / macOS 通知中心，降低打扰；托盘不可用时自动回退气泡")
        form.addRow("系统通知", self._notify_system)
        self._weekly_enabled = QCheckBox("每周回忆录（基于本地聊天生成周报）", box)
        self._weekly_weekday = _NoWheelComboBox(box)
        for idx, label in enumerate(("周一", "周二", "周三", "周四", "周五", "周六", "周日")):
            self._weekly_weekday.addItem(label, idx)
        self._weekly_hour = _int_spin(0, 23, 20, " 点", box)
        form.addRow("周报推送", self._weekly_enabled)
        form.addRow("推送日", self._weekly_weekday)
        form.addRow("推送时刻", self._weekly_hour)
        self._growth_enabled = QCheckBox(
            "成长体系（互动积累等级与形态，右键「陪伴统计」查看）", box)
        form.addRow("成长体系", self._growth_enabled)
        # 关系感：惦记 / 情境 / 可干预 / 久别重逢（全部基于本机事实，可一键退回旧行为）
        self._companion_enabled = QCheckBox("关系感（下面四项的 total 开关）", box)
        self._recall_enabled = QCheckBox("主动提起还没兑现的惦记（每天最多一条）", box)
        self._recall_prompt = QCheckBox("把惦记悄悄告诉 AI，让它聊得自然（不主动推）", box)
        self._companion_contextual = QCheckBox("按周一早晨 / 深夜 / 刚结束专注调整动作与话术", box)
        self._companion_interactive = QCheckBox("摸一下 / 叫一声会打断它当下的动作", box)
        self._reunion_enabled = QCheckBox("久别重逢时它说得出你离开了多久", box)
        self._reunion_hours = _int_spin(1, 720, 20, " 小时", box)
        form.addRow("关系感", self._companion_enabled)
        form.addRow("主动惦记", self._recall_enabled)
        form.addRow("惦记入脑", self._recall_prompt)
        form.addRow("情境化", self._companion_contextual)
        form.addRow("可干预", self._companion_interactive)
        form.addRow("久别问候", self._reunion_enabled)
        form.addRow("久别阈值", self._reunion_hours)
        self._max_recalls = _int_spin(1, 10, 3, " 条", box)
        self._max_recalls.setToolTip("同时惦记几件事；说「搞定了」会自动消解最近一件")
        form.addRow("惦记条数", self._max_recalls)
        return box

    def _build_experience_group(self, parent: QWidget) -> QFrame:
        """体验与隐私：打扰预算 / 行为因果 / 长期记忆 / 写盘权限（P1+P2 的开关面）。"""
        box, form = self._section(parent, "体验与隐私")
        self._quiet_enabled = QCheckBox("打扰预算（限制程序主动推送的每日条数）", box)
        self._quiet_daily = _int_spin(1, 200, 12, " 条/天", box)
        self._quiet_per_source = _int_spin(1, 100, 4, " 条/天", box)
        self._quiet_muted = QCheckBox("一键免打扰（暂停全部主动推送）", box)
        self._quiet_muted.setToolTip("用户主动发起的聊天回复与工具执行反馈不受影响")
        form.addRow("预算开关", self._quiet_enabled)
        form.addRow("每日上限", self._quiet_daily)
        form.addRow("单来源上限", self._quiet_per_source)
        form.addRow("免打扰", self._quiet_muted)
        self._overflow_mode = _NoWheelComboBox(box)
        self._overflow_mode.addItem("额度用完就安静（推荐）", "block")
        self._overflow_mode.addItem("额度用完改降频（不整天静默）", "trickle")
        self._trickle_every = _int_spin(2, 50, 5, " 次/条", box)
        self._trickle_every.setToolTip("降频模式下，每被拦 N 次才放行 1 条")
        form.addRow("超限策略", self._overflow_mode)
        form.addRow("降频间隔", self._trickle_every)
        self._behavior_context = QCheckBox(
            "按当前时段 / 专注 / 勿扰决定动作（而不是纯随机轮播）", box)
        form.addRow("行为驱动", self._behavior_context)
        self._memory_enabled = QCheckBox("长期记忆（记住名字与近况，只存本机）", box)
        self._memory_search_limit = _int_spin(5, 500, 50, " 条", box)
        self._summary_enabled = QCheckBox(
            "会话摘要（被截断的旧对话压缩成一段记忆，聊多久都不失忆）", box)
        form.addRow("长期记忆", self._memory_enabled)
        form.addRow("检索上限", self._memory_search_limit)
        form.addRow("会话摘要", self._summary_enabled)
        self._action_permission = _NoWheelComboBox(box)
        for label, value in (("每次确认（推荐）", "confirm"),
                             ("始终允许", "auto"), ("一律拒绝", "deny")):
            self._action_permission.addItem(label, value)
        self._action_permission.setToolTip("控制模型发起的创建文件 / 生成 PPT 这类写盘操作")
        self._action_remember = QCheckBox("允许在确认框里记住「以后都允许」", box)
        form.addRow("写盘权限", self._action_permission)
        form.addRow("记住授权", self._action_remember)
        # 轴②助理能力：托付 / 资料索引 / 事件驱动主动，全部本机、默认资料索引关闭
        self._assistant_enabled = QCheckBox("助理能力（下面三组的总开关）", box)
        self._tasks_enabled = QCheckBox("可托付任务（盯文件 / 按间隔催，失败会重试）", box)
        self._task_poll = _int_spin(10, 600, 30, " s", box)
        self._use_family_ai = QCheckBox(
            "使用家庭共享 AI（默认勾选：自动跟随管理员共享的 Key；取消则用下面自己的 Key）",
            box)
        self._daily_ai_limit = _int_spin(0, 500, 0, " 次/天", box)
        self._daily_ai_limit.setToolTip("本身份每天最多几次 AI 请求；0 = 不限。"
                                        "超过后回落本地小脑，防孩子过度消费")
        self._knowledge_enabled = QCheckBox("本地资料索引（只在你指定的文件夹里读文本）", box)
        self._knowledge_dir = QLineEdit(box)
        self._knowledge_dir.setPlaceholderText("资料文件夹绝对路径，如 D:/notes（留空=不读）")
        self._knowledge_top_k = _int_spin(1, 8, 3, " 片段", box)
        self._semantic_search = QCheckBox(
            "语义检索（按意思找资料；需在 AI 服务里填 Embedding 模型）", box)
        self._events_enabled = QCheckBox("按本机情境主动关心（回来 / 深夜 / 连着专注）", box)
        self._event_limit = _int_spin(0, 10, 2, " 条/天", box)
        # 联网能力：web_search 与 IP 定位（get_location），默认开启可关
        self._web_search_enabled = QCheckBox("联网搜索（AI 可查实时网页信息）", box)
        self._web_search_provider = _NoWheelComboBox(box)
        for label, value in (("必应 Bing（免 Key，国内直连，推荐）", "bing"),
                             ("DuckDuckGo（免 Key，国内网络通常不通）", "duckduckgo"),
                             ("Tavily（海外效果，填 Key）", "tavily"),
                             ("博查（国内效果好，填 Key）", "bocha")):
            self._web_search_provider.addItem(label, value)
        self._web_search_key = QLineEdit(box)
        self._web_search_key.setEchoMode(QLineEdit.Password)
        self._web_search_key.setPlaceholderText("所选供应商的 API Key（DuckDuckGo 不用填）")
        self._amap_key = QLineEdit(box)
        self._amap_key.setEchoMode(QLineEdit.Password)
        self._amap_key.setPlaceholderText("高德 Web 服务 Key（填了才能查附近的店铺，lbs.amap.com 免费申请）")
        self._location_enabled = QCheckBox("位置权限（AI 查「附近」时取 IP 所在城市）", box)
        self._location_consent = _NoWheelComboBox(box)
        for label, value in (("每次询问（弹窗问我要不要给）", "ask"),
                             ("总是允许（不再弹窗）", "granted"),
                             ("不允许（AI 拿不到位置）", "denied")):
            self._location_consent.addItem(label, value)
        # 主动天气播报：默认关闭，开了才联网（国内天气源免 Key）
        self._weather_enabled = QCheckBox("定时播报天气（联网取，免 Key）", box)
        self._weather_interval = _int_spin(30, 1440, 180, " 分钟", box)
        self._weather_bad_alert = QCheckBox("雨雪天额外提醒一次", box)
        form.addRow("助理能力", self._assistant_enabled)
        form.addRow("可托付", self._tasks_enabled)
        form.addRow("盯文件间隔", self._task_poll)
        form.addRow("家庭共享 AI", self._use_family_ai)
        form.addRow("每日 AI 上限", self._daily_ai_limit)
        form.addRow("资料索引", self._knowledge_enabled)
        form.addRow("资料文件夹", self._knowledge_dir)
        form.addRow("注入片段", self._knowledge_top_k)
        form.addRow("语义检索", self._semantic_search)
        form.addRow("事件主动", self._events_enabled)
        form.addRow("情境上限", self._event_limit)
        form.addRow("联网搜索", self._web_search_enabled)
        form.addRow("搜索源", self._web_search_provider)
        form.addRow("搜索 Key", self._web_search_key)
        form.addRow("高德 Key", self._amap_key)
        form.addRow("位置权限", self._location_enabled)
        form.addRow("授权策略", self._location_consent)
        form.addRow("天气播报", self._weather_enabled)
        form.addRow("播报间隔", self._weather_interval)
        form.addRow("坏天气提醒", self._weather_bad_alert)
        return box

    def _build_storage_group(self, parent: QWidget) -> QFrame:
        """数据存储位置：显示当前路径、可选新目录、浏览按钮。改位置需迁移+重启。"""
        browse = QPushButton("浏览…", parent)
        browse.setObjectName("inputOk")
        browse.setToolTip("选一个已存在的文件夹存放 WisePet 的全部数据")
        browse.clicked.connect(self._on_browse_data_dir)
        box, form = self._section(parent, "数据存储位置", actions=[browse])
        current = QLabel(self._current_data_dir or "（未检测）", box)
        current.setObjectName("sectionNote")
        current.setWordWrap(True)
        self._data_dir_current = current
        self._data_dir_edit = QLineEdit(box)
        self._data_dir_edit.setPlaceholderText(
            "留空=用当前位置；填绝对路径可换位置（保存后需重启，数据会复制过去）")
        form.addRow("当前位置", current)
        form.addRow("改为", self._data_dir_edit)
        note = QLabel(
            "所有记忆、聊天记录（数据库）、配置都在这里；换位置会把旧数据复制过去，"
            "原目录保留作备份。家庭共享 Key 也随新位置走。", box)
        note.setWordWrap(True)
        note.setObjectName("sectionNote")
        form.addRow("", note)
        return box

    def _on_browse_data_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "选择数据存储位置", self._data_dir_edit.text().strip()
            or self._current_data_dir or str(Path.home()))
        if chosen:
            self._data_dir_edit.setText(chosen)

    def _build_system_group(self, parent: QWidget) -> QFrame:
        """系统配置：与宠物本身无关的运行参数（当前是日志治理四项）。"""
        box, form = self._section(parent, "系统配置")
        self._log_level = _NoWheelComboBox(box)
        for label, value in (("详细（DEBUG）", "debug"),
                             ("常规（INFO，推荐）", "info"),
                             ("仅告警（WARNING）", "warning"),
                             ("仅错误（ERROR）", "error")):
            self._log_level.addItem(label, value)
        self._log_max_mb = _int_spin(1, 100, 10, " MiB", box)
        self._log_max_mb.setToolTip("单个日志文件超过这个值就分割")
        self._log_retention = _int_spin(1, 30, 3, " 天", box)
        self._log_retention.setToolTip("滚动备份只保留近 N 天，正在写的文件不动")
        self._log_split = QCheckBox("告警与错误另存一份 WisePet-error.log", box)
        form.addRow("日志级别", self._log_level)
        form.addRow("单文件上限", self._log_max_mb)
        form.addRow("保留天数", self._log_retention)
        form.addRow("日志分档", self._log_split)
        # 自动更新：可选，默认关；检查按钮由组装根接回调（见 set_update_callback）
        self._auto_check_update = QCheckBox("启动时检查新版本（后台，不打扰）", box)
        self._update_repo = QLineEdit(box)
        self._update_repo.setPlaceholderText("GitHub 仓库，如 owner/repo（留空不检查）")
        self._check_update_btn = QPushButton("立即检查更新", box)
        self._check_update_btn.setObjectName("inputOk")
        self._check_update_btn.clicked.connect(self._on_check_update_clicked)
        self._update_callback = None
        form.addRow("自动更新", self._auto_check_update)
        form.addRow("更新源", self._update_repo)
        form.addRow("", self._check_update_btn)
        note = QLabel("保存后立即生效，无需重启；设置放在 ~/.WisePet/config.json 的 system 分节。",
                      box)
        note.setWordWrap(True)
        note.setObjectName("sectionNote")
        form.addRow("", note)
        return box

    def set_update_callback(self, callback) -> None:
        """组装根注入「立即检查更新」回调（接收当前输入的 repo 字符串）。"""
        self._update_callback = callback

    def _on_check_update_clicked(self) -> None:
        if self._update_callback is not None:
            self._update_callback(self._update_repo.text().strip())

    # ---------- 取值 / 回填 ----------

    def _load_from(self, cfg: AppConfig) -> None:
        self._profiles = list(cfg.ai_profiles) or [AIProfile.from_ai(cfg.ai)]
        self._active_profile_name = self._match_active_profile(cfg)
        self._form_profile_name = ""
        self._profile_combo.blockSignals(True)
        self._profile_combo.clear()
        self._profile_combo.addItems([p.name for p in self._profiles])
        if self._active_profile_name in [p.name for p in self._profiles]:
            self._profile_combo.setCurrentText(self._active_profile_name)
        self._profile_combo.blockSignals(False)
        self._load_profile_fields(self._profile_combo.currentText())
        self._temperature.setValue(cfg.ai.temperature)
        self._timeout.setValue(int(cfg.ai.timeout_s))
        self._embedding_model.setText(cfg.ai.embedding_model)
        self._share_family.setChecked(
            getattr(getattr(cfg, "assistant", None), "share_family_ai", False)
            if self._is_manager else False)
        self._persona_name.setText(cfg.persona.name)
        self._system_prompt.setPlainText(cfg.persona.system_prompt)
        self._persona_anchor.setChecked(
            bool(getattr(cfg.persona, "anchor_enabled", True)))
        self._walk_speed.setValue(cfg.pet.walk_speed_px_per_s)
        self._idle_min.setValue(int(cfg.pet.idle_min_s))
        self._idle_max.setValue(int(cfg.pet.idle_max_s))
        self._max_turns.setValue(int(cfg.pet.max_turns))
        self._chat_idle_hide.setValue(
            int(cfg.chat.idle_hide_s) if getattr(cfg, "chat", None) is not None else 30)
        self._stick_to_floor.setChecked(cfg.pet.stick_to_floor)
        self._roam.setChecked(getattr(cfg.pet, "roam", True))
        self._roam_jump_interval.setValue(int(getattr(cfg.pet, "roam_jump_interval_s", 30)))
        activity_index = self._activity.findData(
            getattr(cfg.pet, "activity", "normal"))
        if activity_index >= 0:
            self._activity.setCurrentIndex(activity_index)
        enter_index = self._enter_effect.findData(
            getattr(cfg.pet, "enter_effect", "auto"))
        if enter_index >= 0:
            self._enter_effect.setCurrentIndex(enter_index)
        index = self._skin.findData(normalize_skin(cfg.pet.skin))
        if index >= 0:
            self._skin.setCurrentIndex(index)
        self._water_enabled.setChecked(cfg.reminders.water.enabled)
        self._water_interval.setValue(int(cfg.reminders.water.interval_min))
        self._sedentary_enabled.setChecked(cfg.reminders.sedentary.enabled)
        self._sedentary_interval.setValue(int(cfg.reminders.sedentary.interval_min))
        self._news_enabled.setChecked(getattr(cfg, "news", None) is not None
                                      and cfg.news.enabled)
        self._news_interval.setValue(int(cfg.news.interval_min))
        self._cheer_enabled.setChecked(getattr(cfg, "cheer", None) is not None
                                       and cfg.cheer.enabled)
        self._cheer_interval.setValue(int(cfg.cheer.interval_min))
        dnd = getattr(cfg, "dnd", None)
        self._dnd_enabled.setChecked(dnd.enabled if dnd is not None else False)
        self._dnd_start.setValue(int(dnd.start_hour) if dnd is not None else 12)
        self._dnd_end.setValue(int(dnd.end_hour) if dnd is not None else 14)
        self._focus_default.setValue(
            int(cfg.focus.default_min) if getattr(cfg, "focus", None) is not None else 25)
        self._bubble_duration.setValue(
            int(cfg.bubble.duration_s) if getattr(cfg, "bubble", None) is not None else 15)
        tts = getattr(cfg, "tts", None)
        self._tts_enabled.setChecked(tts.enabled if tts is not None else False)
        self._tts_volume.setValue(int(tts.volume) if tts is not None else 100)
        self._tts_rate.setValue(int(tts.rate) if tts is not None else 0)
        notification = getattr(cfg, "notification", None)
        self._notify_system.setChecked(notification.use_system
                                        if notification is not None else False)
        weekly = getattr(cfg, "weekly", None)
        self._weekly_enabled.setChecked(weekly.enabled if weekly is not None else True)
        wday = int(weekly.weekday) if weekly is not None else 6
        widx = self._weekly_weekday.findData(wday)
        if widx >= 0:
            self._weekly_weekday.setCurrentIndex(widx)
        self._weekly_hour.setValue(int(weekly.hour) if weekly is not None else 20)
        growth = getattr(cfg, "growth", None)
        self._growth_enabled.setChecked(growth.enabled if growth is not None else True)
        companion = getattr(cfg, "companion", None)
        self._companion_enabled.setChecked(
            companion.enabled if companion is not None else True)
        self._recall_enabled.setChecked(
            companion.recall_enabled if companion is not None else True)
        self._recall_prompt.setChecked(
            companion.memory_prompt_enabled if companion is not None else True)
        self._companion_contextual.setChecked(
            companion.contextual if companion is not None else True)
        self._companion_interactive.setChecked(
            companion.interactive if companion is not None else True)
        self._reunion_enabled.setChecked(
            companion.reunion_enabled if companion is not None else True)
        self._reunion_hours.setValue(
            int(companion.reunion_min_hours) if companion is not None else 20)
        self._max_recalls.setValue(
            int(getattr(companion, "max_recalls", 3)) if companion is not None else 3)
        assistant = getattr(cfg, "assistant", None)
        self._assistant_enabled.setChecked(
            assistant.enabled if assistant is not None else True)
        self._tasks_enabled.setChecked(
            assistant.tasks_enabled if assistant is not None else True)
        self._task_poll.setValue(
            int(assistant.task_poll_s) if assistant is not None else 30)
        self._use_family_ai.setChecked(
            assistant.use_family_ai if assistant is not None else False)
        self._daily_ai_limit.setValue(
            int(assistant.daily_ai_limit) if assistant is not None else 0)
        self._knowledge_enabled.setChecked(
            assistant.knowledge_enabled if assistant is not None else False)
        self._knowledge_dir.setText(
            assistant.knowledge_dir if assistant is not None else "")
        self._knowledge_top_k.setValue(
            int(assistant.knowledge_top_k) if assistant is not None else 3)
        self._semantic_search.setChecked(
            assistant.semantic_search if assistant is not None else False)
        self._events_enabled.setChecked(
            assistant.events_enabled if assistant is not None else True)
        self._event_limit.setValue(
            int(assistant.event_daily_limit) if assistant is not None else 2)
        web = getattr(cfg, "web_search", None)
        self._web_search_enabled.setChecked(
            web.enabled if web is not None else True)
        prov_index = self._web_search_provider.findData(
            getattr(web, "provider", "bing") if web is not None
            else "bing")
        if prov_index >= 0:
            self._web_search_provider.setCurrentIndex(prov_index)
        self._web_search_key.setText(
            getattr(web, "api_key", "") if web is not None else "")
        self._amap_key.setText(
            getattr(web, "amap_key", "") if web is not None else "")
        location = getattr(cfg, "location", None)
        self._location_enabled.setChecked(
            location.enabled if location is not None else True)
        consent_index = self._location_consent.findData(
            getattr(location, "consent", "ask") if location is not None else "ask")
        if consent_index >= 0:
            self._location_consent.setCurrentIndex(consent_index)
        weather = getattr(cfg, "weather", None)
        self._weather_enabled.setChecked(
            weather.enabled if weather is not None else False)
        # 兼容旧固定时刻配置：有 report_hour 时换算成「每 24 小时一次」
        old_hour = getattr(weather, "report_hour", None) if weather is not None else None
        interval = (1440 if old_hour is not None
                    else int(getattr(weather, "interval_min", 180)
                             if weather is not None else 180))
        self._weather_interval.setValue(interval)
        self._weather_bad_alert.setChecked(
            bool(getattr(weather, "bad_weather_alert", True)
                 if weather is not None else True))
        quiet = getattr(cfg, "quiet", None)
        self._quiet_enabled.setChecked(quiet.enabled if quiet is not None else True)
        self._quiet_daily.setValue(int(quiet.daily_limit) if quiet is not None else 12)
        self._quiet_per_source.setValue(
            int(quiet.per_source_limit) if quiet is not None else 4)
        self._quiet_muted.setChecked(quiet.muted if quiet is not None else False)
        mode_index = self._overflow_mode.findData(
            getattr(quiet, "overflow_mode", "block") if quiet is not None else "block")
        if mode_index >= 0:
            self._overflow_mode.setCurrentIndex(mode_index)
        self._trickle_every.setValue(
            int(getattr(quiet, "trickle_every", 5)) if quiet is not None else 5)
        behavior = getattr(cfg, "behavior", None)
        self._behavior_context.setChecked(
            behavior.context_driven if behavior is not None else True)
        memory = getattr(cfg, "memory", None)
        self._memory_enabled.setChecked(memory.enabled if memory is not None else True)
        self._memory_search_limit.setValue(
            int(memory.search_max_results) if memory is not None else 50)
        self._summary_enabled.setChecked(
            getattr(memory, "summary_enabled", True) if memory is not None else True)
        actions = getattr(cfg, "actions", None)
        pidx = self._action_permission.findData(
            actions.write_permission if actions is not None else "confirm")
        if pidx >= 0:
            self._action_permission.setCurrentIndex(pidx)
        self._action_remember.setChecked(
            actions.remember_allowed if actions is not None else False)
        system = getattr(cfg, "system", None)
        level_index = self._log_level.findData(
            system.log_level if system is not None else "info")
        if level_index >= 0:
            self._log_level.setCurrentIndex(level_index)
        self._log_max_mb.setValue(
            int(system.log_max_mb) if system is not None else 10)
        self._log_retention.setValue(
            int(system.log_retention_days) if system is not None else 3)
        self._log_split.setChecked(
            system.log_split_by_level if system is not None else True)
        self._auto_check_update.setChecked(
            bool(system.auto_check_update) if system is not None else False)
        self._update_repo.setText(
            str(getattr(system, "update_repo", "") or "") if system is not None else "")
        self._sync_reminder_labels()

    def _sync_reminder_labels(self) -> None:
        """自定义 indicator 只换背景色、没画对勾，靠文字把启用状态说清楚。"""
        for check in (self._water_enabled, self._sedentary_enabled):
            check.setText("已启用" if check.isChecked() else "未启用")

    def collect(self) -> tuple[AppConfig | None, str]:
        """按当前表单构造新配置；返回 (配置, 错误信息)，错误非空时配置为 None。"""
        if self._idle_min.value() > self._idle_max.value():
            return None, "闲置间隔下限不能大于上限"
        minimum = int(self._base.reminders.min_interval_min)
        if self._water_interval.value() < minimum or \
                self._sedentary_interval.value() < minimum:
            return None, f"提醒间隔不能小于 {minimum} 分钟"

        profiles = self._flush_form_to_profile()
        active = next((p for p in profiles if p.name == self._active_profile_name),
                      profiles[0])
        ai = replace(self._base.ai,
                     base_url=active.base_url,
                     api_key=active.api_key,
                     model=active.model,
                     embedding_model=self._embedding_model.text().strip(),
                     purpose=normalize_purpose(getattr(active, "purpose", "")),
                     temperature=float(self._temperature.value()),
                     timeout_s=float(self._timeout.value()))
        # 管理员专属：共享状态持久化在 assistant 分节（文件落盘由组装根处理）
        assistant_share = (self._share_family.isChecked() if self._is_manager
                           else self._base.assistant.share_family_ai)
        persona = PersonaConfig(name=self._persona_name.text().strip()
                                or self._base.persona.name,
                                system_prompt=self._system_prompt.toPlainText().strip()
                                or self._base.persona.system_prompt,
                                anchor_enabled=self._persona_anchor.isChecked())
        # 用 replace 而不是重建 PetParams：v2.0 的位移/概率参数不在表单上，
        # 重建会把它们全部静默打回默认值（贴地开关也就永远关不掉）。
        pet = replace(self._base.pet,
                      skin=self._skin.currentData() or DEFAULT_SKIN,
                      activity=self._activity.currentData() or "normal",
                      enter_effect=self._enter_effect.currentData() or "auto",
                      walk_speed_px_per_s=float(self._walk_speed.value()),
                      idle_min_s=float(self._idle_min.value()),
                      idle_max_s=float(self._idle_max.value()),
                      max_turns=int(self._max_turns.value()),
                      stick_to_floor=self._stick_to_floor.isChecked(),
                      roam=self._roam.isChecked(),
                      roam_jump_interval_s=float(self._roam_jump_interval.value()))
        reminders = ReminderSettings(
            water=replace(self._base.reminders.water,
                          enabled=self._water_enabled.isChecked(),
                          interval_min=float(self._water_interval.value())),
            sedentary=replace(self._base.reminders.sedentary,
                              enabled=self._sedentary_enabled.isChecked(),
                              interval_min=float(self._sedentary_interval.value())),
            snooze_min=self._base.reminders.snooze_min,
            min_interval_min=self._base.reminders.min_interval_min,
        )
        news = replace(self._base.news,
                       enabled=self._news_enabled.isChecked(),
                       interval_min=float(self._news_interval.value()))
        cheer = replace(self._base.cheer,
                        enabled=self._cheer_enabled.isChecked(),
                        interval_min=float(self._cheer_interval.value()))
        bubble = BubbleSettings(duration_s=float(self._bubble_duration.value()))
        chat = replace(self._base.chat, idle_hide_s=float(self._chat_idle_hide.value()))
        dnd = replace(self._base.dnd,
                      enabled=self._dnd_enabled.isChecked(),
                      start_hour=int(self._dnd_start.value()),
                      end_hour=int(self._dnd_end.value()))
        focus = replace(self._base.focus,
                        default_min=float(self._focus_default.value()))
        tts = replace(self._base.tts,
                      enabled=self._tts_enabled.isChecked(),
                      volume=int(self._tts_volume.value()),
                      rate=int(self._tts_rate.value()))
        notification = replace(self._base.notification,
                               use_system=self._notify_system.isChecked())
        weekly = replace(self._base.weekly,
                         enabled=self._weekly_enabled.isChecked(),
                         weekday=int(self._weekly_weekday.currentData()),
                         hour=int(self._weekly_hour.value()))
        growth = replace(self._base.growth,
                         enabled=self._growth_enabled.isChecked())
        quiet = replace(self._base.quiet,
                        enabled=self._quiet_enabled.isChecked(),
                        daily_limit=int(self._quiet_daily.value()),
                        per_source_limit=int(self._quiet_per_source.value()),
                        muted=self._quiet_muted.isChecked(),
                        overflow_mode=self._overflow_mode.currentData() or "block",
                        trickle_every=int(self._trickle_every.value()))
        behavior = replace(self._base.behavior,
                           context_driven=self._behavior_context.isChecked())
        memory = replace(self._base.memory,
                         enabled=self._memory_enabled.isChecked(),
                         search_max_results=int(self._memory_search_limit.value()),
                         summary_enabled=self._summary_enabled.isChecked())
        actions = replace(self._base.actions,
                          write_permission=self._action_permission.currentData()
                          or "confirm",
                          remember_allowed=self._action_remember.isChecked())
        system = replace(self._base.system,
                         log_level=self._log_level.currentData() or "info",
                         log_max_mb=int(self._log_max_mb.value()),
                         log_retention_days=int(self._log_retention.value()),
                         log_split_by_level=self._log_split.isChecked(),
                         auto_check_update=self._auto_check_update.isChecked(),
                         update_repo=self._update_repo.text().strip()[:100],
                         data_dir=self._data_dir_edit.text().strip()[:500]
                         or self._base.system.data_dir)
        companion = replace(self._base.companion,
                            enabled=self._companion_enabled.isChecked(),
                            recall_enabled=self._recall_enabled.isChecked(),
                            memory_prompt_enabled=self._recall_prompt.isChecked(),
                            contextual=self._companion_contextual.isChecked(),
                            interactive=self._companion_interactive.isChecked(),
                            reunion_enabled=self._reunion_enabled.isChecked(),
                            reunion_min_hours=float(self._reunion_hours.value()),
                            max_recalls=int(self._max_recalls.value()))
        assistant = replace(self._base.assistant,
                            enabled=self._assistant_enabled.isChecked(),
                            tasks_enabled=self._tasks_enabled.isChecked(),
                            task_poll_s=float(self._task_poll.value()),
                            use_family_ai=self._use_family_ai.isChecked(),
                            share_family_ai=assistant_share,
                            daily_ai_limit=int(self._daily_ai_limit.value()),
                            knowledge_enabled=self._knowledge_enabled.isChecked(),
                            knowledge_dir=self._knowledge_dir.text().strip(),
                            knowledge_top_k=int(self._knowledge_top_k.value()),
                            semantic_search=self._semantic_search.isChecked(),
                            events_enabled=self._events_enabled.isChecked(),
                            event_daily_limit=int(self._event_limit.value()))
        web_search = WebSearchSettings(
            enabled=self._web_search_enabled.isChecked(),
            provider=self._web_search_provider.currentData() or "bing",
            api_key=self._web_search_key.text().strip(),
            amap_key=self._amap_key.text().strip(),
            max_results=int(getattr(self._base.web_search, "max_results", 5)))
        location = LocationSettings(
            enabled=self._location_enabled.isChecked(),
            consent=self._location_consent.currentData() or "ask")
        weather = WeatherSettings(
            enabled=self._weather_enabled.isChecked(),
            interval_min=float(self._weather_interval.value()),
            bad_weather_alert=self._weather_bad_alert.isChecked())
        return AppConfig(ai=ai, ai_profiles=profiles, persona=persona, pet=pet,
                         vitals=self._base.vitals, reminders=reminders,
                         news=news, cheer=cheer, bubble=bubble, chat=chat,
                         focus=focus, dnd=dnd, tts=tts, notification=notification,
                         weekly=weekly, growth=growth, quiet=quiet,
                         behavior=behavior, memory=memory, actions=actions,
                         system=system, companion=companion,
                         assistant=assistant, web_search=web_search,
                         location=location, weather=weather), ""

    def _match_active_profile(self, cfg: AppConfig) -> str:
        """当前生效 ai 对应的方案名；找不到就用第一个方案。

        只按 base_url + model 匹配：家庭共享会替换 api_key，比 key 必然对不上。
        """
        for profile in self._profiles:
            if (profile.base_url == cfg.ai.base_url
                    and profile.model == cfg.ai.model):
                return profile.name
        return self._profiles[0].name

    # ---------- 动作 ----------

    def _save(self) -> None:
        cfg, error = self.collect()
        if cfg is None:
            self._show_status(error, ok=False)
            return
        # 数据目录变更：走专门回调（迁移+写指针+提示重启），不走普通热保存
        target = self._data_dir_edit.text().strip()
        if target and target != self._current_data_dir:
            if self._on_data_dir_change is None:
                self._show_status("当前不支持修改数据目录", ok=False)
                return
            result = self._on_data_dir_change(target)
            self._show_status(result, ok="失败" not in result and "取消" not in result)
            if "失败" not in result and "取消" not in result:
                self.accept()
            return
        if self._on_saved is not None:
            try:
                self._on_saved(cfg)
            except OSError as exc:  # 磁盘/权限异常：提示用户但不崩（PRD 可靠性）
                logger.exception("config save failed")
                self._show_status(f"保存失败：{exc}", ok=False)
                return
        self._show_status(f"已保存（api_key：{mask_key(cfg.ai.api_key) or '未配置，暂用本地小脑'}）",
                          ok=True)
        self.accept()

    def _test_connection(self) -> None:
        cfg, error = self.collect()
        if cfg is None:
            self._show_status(error, ok=False)
            return
        self._show_status("正在连接…", ok=True)
        try:
            probe(cfg.ai)
        except AIGatewayError as exc:
            self._show_status(f"连接失败：{ChatService.fallback_text(exc.kind, exc.detail)}"
                              f"（{exc.detail}）", ok=False)
            return
        self._show_status("连接成功 ✓", ok=True)

    def _show_status(self, text: str, *, ok: bool) -> None:
        self._status.setText(text)
        self._status.setStyleSheet(_STATUS_OK if ok else _STATUS_ERROR)
        self._status.show()

    # ---------- 无边框窗口的标题栏拖动 ----------

    @property
    def close_button(self) -> QPushButton:
        return self._close_btn

    @property
    def title_bar(self) -> QFrame:
        """测试注入真实鼠标事件用（FR v1.13 C23）。"""
        return self._title_bar

    def _begin_drag(self, global_pos: QPoint) -> None:
        self._drag_global = global_pos
        self._drag_window_pos = self.pos()

    def _end_drag(self) -> None:
        self._drag_global = None
        self._drag_window_pos = None

    def _move_by(self, global_pos: QPoint) -> None:
        if self._drag_global is None or self._drag_window_pos is None:
            return
        target = self._drag_window_pos + global_pos - self._drag_global
        if self._screen is not None:
            x, y = self._screen.clamp(target.x(), target.y(),
                                      self.width(), self.height())
        else:
            x, y = target.x(), target.y()
        self.move(x, y)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt 重写
        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self._begin_drag(event.globalPosition().toPoint())
        elif event.type() == QEvent.MouseMove and self._drag_global is not None:
            self._move_by(event.globalPosition().toPoint())
        elif event.type() == QEvent.MouseButtonRelease:
            self._end_drag()
        return super().eventFilter(obj, event)


def _int_spin(minimum: int, maximum: int, value: int, suffix: str,
              parent: QWidget) -> QSpinBox:
    """构整数值微调框（表单里大量重复，抽公函数避免散落的 setter）。"""
    spin = _NoWheelSpinBox(parent)
    spin.setRange(minimum, maximum)
    spin.setValue(value)
    spin.setSuffix(suffix)
    return spin
