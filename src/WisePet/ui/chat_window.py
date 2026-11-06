"""历史对话查看器：按天回看 + 底部继续聊、贴纸风圆角卡片（FR v1.10 C18/C19）。

由右键菜单「历史对话…」打开，打开时收起单行输入条；本窗自带输入行可以接着聊。
只负责显示与发信号；仓储读写由组装根注入的回调提供，ui 不直接碰 infra（规约 3）。
"""
from __future__ import annotations

import html
import logging
from datetime import datetime
from typing import Callable

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QPushButton,
    QTextBrowser, QVBoxLayout, QWidget,
)

from ..domain.attachment import MAX_ATTACHMENTS, Attachment, load_attachment
from ..domain.chat_record import day_key
from ..domain.events import (
    PHASE_DONE, PHASE_ERROR, PHASE_IDLE, PHASE_REPLYING, PHASE_THINKING,
)
from .chat_text_edit import ChatTextEdit, attachment_from_clipboard_image
from .screen_bounds import ScreenBounds, fit_beside_width

logger = logging.getLogger(__name__)

_WINDOW_WIDTH = 440
_WINDOW_HEIGHT = 500
_WINDOW_MIN_WIDTH = 300

#: 会话阶段 → 用户可读文案；错误另拼 detail
_PHASE_TEXT = {
    PHASE_IDLE: "空闲，随时可以聊",
    PHASE_THINKING: "思考中…",
    PHASE_REPLYING: "正在回复…",
    PHASE_DONE: "回复完成",
    PHASE_ERROR: "出错了",
}
_BRAIN_TEXT = {"ai": "大模型", "local": "本地小脑", "": "待确定"}
_ROLE_TEXT = {"user": "你", "assistant": "小墨", "system": "系统"}
_SOURCE_TEXT = {"ai": "大模型", "local": "本地小脑", "fallback": "兜底话术",
                "user": "", "system": ""}

#: 气泡配色：用户暖橘、助手奶油白、兜底淡粉
_BUBBLE_BG = {"user": "#F8D79B", "assistant": "#FFFFFF", "fallback": "#F7E2E2"}
_BUBBLE_BORDER = "#E9D6B6"

_STYLE = """
QFrame#historyCard {
    background: #FFFDF7;
    border: 1px solid #DFC49A;
    border-radius: 16px;
}
QLabel#title { color: #5A3E28; font-size: 15px; font-weight: 600; }
QLabel#dot { color: #E8A45C; font-size: 16px; }
QLabel#status {
    color: #9A7A55; font-size: 11px;
    background: #F7EEDC; border-radius: 9px; padding: 4px 10px;
}
QLabel#hint { color: #B09A7C; font-size: 10px; }
QComboBox {
    background: #FFFFFF; border: 1px solid #E2CFAF; border-radius: 9px;
    padding: 4px 10px; color: #6B4F3A; font-size: 11px;
}
QComboBox::drop-down { border: 0; width: 16px; }
QComboBox QAbstractItemView {
    background: #FFFDF7; border: 1px solid #E2CFAF;
    selection-background-color: #F7E7CC; color: #3A2B21;
}
QPushButton#close {
    background: transparent; border: none; color: #A08A6C;
    font-size: 18px; border-radius: 10px; min-width: 26px; min-height: 26px;
}
QPushButton#close:hover { background: #F3E1C6; color: #6B4F3A; }
QTextBrowser {
    background: #FFFBF2; border: 1px solid #EFE0C6; border-radius: 12px;
    padding: 10px; color: #3A2B21;
}
QTextBrowser QScrollBar:vertical { background: transparent; width: 8px; margin: 0; }
QTextBrowser QScrollBar::handle:vertical {
    background: #E2CFAF; border-radius: 4px; min-height: 24px;
}
QTextBrowser QScrollBar::add-line:vertical,
QTextBrowser QScrollBar::sub-line:vertical { height: 0; }
QTextEdit#chatEdit {
    background: #FFFFFF; border: 1px solid #E7D3B2; border-radius: 9px;
    padding: 5px 10px; color: #3A2B21; font-size: 12px;
    selection-background-color: #F3D9A8;
}
QTextEdit#chatEdit:focus { border: 1px solid #E8A45C; }
QLabel#files { color: #B0885C; font-size: 10px; padding: 0 16px; }
QPushButton#attach {
    background: transparent; border: none; color: #A08A6C; font-size: 15px;
    min-width: 26px; padding: 4px;
}
QPushButton#attach:hover { color: #6B4F3A; }
QPushButton#send {
    background: #F8D79B; border: 1px solid #E7C79A; border-radius: 9px;
    color: #6B4F3A; font-size: 12px; padding: 6px 14px;
}
QPushButton#send:hover { background: #F3D9A8; }
"""


def _clock_of(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M") if ts else "--:--"


def _bubble_html(record) -> str:
    """一条消息 = 一行小字元信息 + 一个带底色的气泡（Qt 富文本兼容写法）。"""
    mine = record.role == "user"
    bg = _BUBBLE_BG.get("fallback" if record.source == "fallback" else record.role,
                        _BUBBLE_BG["assistant"])
    align = "right" if mine else "left"
    role = _ROLE_TEXT.get(record.role, record.role)
    source = _SOURCE_TEXT.get(record.source, "")
    meta = f"{_clock_of(record.ts)} · {role}" + (f" · {source}" if source else "")
    content = html.escape(record.content).replace("\n", "<br>")
    return (
        '<table width="100%" cellpadding="0" cellspacing="0" '
        'style="margin:5px 0 12px 0;">'
        f'<tr><td align="{align}" style="color:#A08A6C;font-size:10px;">'
        f'{html.escape(meta)}</td></tr>'
        f'<tr><td align="{align}">'
        f'<table cellpadding="9" cellspacing="0" bgcolor="{bg}" '
        f'style="background-color:{bg};border:1px solid {_BUBBLE_BORDER};">'
        f'<tr><td style="color:#3A2B21;">{content}</td></tr>'
        '</table></td></tr></table>'
    )


class ChatWindow(QWidget):
    """历史窗：圆角卡片 + 日期切换 + 气泡消息 + 底部输入行；开关发 active_changed。"""

    #: 会话激活状态变化 → 组装根据此冻结/恢复宠物位移
    active_changed = Signal(bool)
    #: 底部输入行 → 主程序转交 ChatService（与单行输入条同一个出口）
    submitted = Signal(str)
    #: 用户在下拉里切了模型方案 → 主程序热切换 AI 配置
    model_selected = Signal(str)

    def __init__(self, screen_bounds: ScreenBounds | None = None,
                 parent: QWidget | None = None,
                 days_provider: Callable[[], list[str]] | None = None,
                 records_provider: Callable[[str], list] | None = None):
        super().__init__(parent)
        self._screen = screen_bounds
        self._days_provider = days_provider or (lambda: [day_key()])
        self._records_provider = records_provider or (lambda _day: [])
        self._active = False
        self._phase = PHASE_IDLE
        self._brain = ""
        self._detail = ""
        self._count = 0
        self._empty_hint = False
        self._drag_global: QPoint | None = None
        self._drag_window_pos: QPoint | None = None
        self._pinned = False               # 用户手动拖过之后不再被宠物位置拽回
        self._attachments: list[Attachment] = []

        self.setWindowTitle("WisePet 历史对话")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        # 不固定宽度：宠物在窄工作区中间时要能缩到「较宽那侧」放得下
        self.setMinimumWidth(_WINDOW_MIN_WIDTH)
        self.resize(_WINDOW_WIDTH, _WINDOW_HEIGHT)
        self.setStyleSheet(_STYLE)

        card = QFrame(self)
        card.setObjectName("historyCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

        dot = QLabel("●", card)
        dot.setObjectName("dot")
        title = QLabel("聊天记录", card)
        title.setObjectName("title")
        self._day_combo = QComboBox(card)
        self._day_combo.addItem(day_key())        # 未 reload 前也以「今天」为当前视图
        self._day_combo.currentTextChanged.connect(self._on_day_changed)
        self._model_combo = QComboBox(card)
        self._model_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents)   # 宽度跟最长的方案名走
        self._model_combo.setToolTip("切换 AI 模型方案（右键→设置里可增删方案）")
        model_label = QLabel("模型", card)
        model_label.setObjectName("hint")
        self._model_combo.currentTextChanged.connect(
            lambda name: self.model_selected.emit(name))
        self._close_btn = QPushButton("×", card)
        self._close_btn.setObjectName("close")
        self._close_btn.setToolTip("关闭")
        self._close_btn.clicked.connect(self.hide)

        head = QHBoxLayout()
        head.setContentsMargins(16, 14, 12, 6)
        head.setSpacing(8)
        head.addWidget(dot)
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self._day_combo)
        head.addWidget(model_label)
        head.addWidget(self._model_combo)
        head.addWidget(self._close_btn)
        # 无边框窗口没有系统标题栏，标题区（含 dot/标题/card 空白处）负责拖动
        for widget in (card, dot, title):
            widget.installEventFilter(self)

        self._status = QLabel(card)
        self._status.setObjectName("status")
        self._status.setWordWrap(True)
        status_row = QHBoxLayout()
        status_row.setContentsMargins(16, 0, 16, 10)
        status_row.addWidget(self._status, 1)

        self._history = QTextBrowser(card)
        self._history.setReadOnly(True)
        self._history.setOpenExternalLinks(False)
        self._edit = ChatTextEdit(card)
        self._edit.setObjectName("chatEdit")
        self._edit.setPlaceholderText("接着聊点什么…（回车发送，Shift+回车换行，Esc 关闭）")
        self._edit.send_requested.connect(self._submit)
        self._edit.dismissed.connect(self.hide)
        self._edit.image_pasted.connect(self._add_clipboard_image)
        self._attach_btn = QPushButton("📎", card)
        self._attach_btn.setObjectName("attach")
        self._attach_btn.setToolTip("附加图片或文本文件（图片需视觉模型）")
        self._attach_btn.setCursor(Qt.PointingHandCursor)
        self._attach_btn.clicked.connect(self._pick_files)
        self._files = QLabel(card)
        self._files.setObjectName("files")
        self._files.setWordWrap(True)
        self._files.hide()
        self._send_btn = QPushButton("发送", card)
        self._send_btn.setObjectName("send")
        self._send_btn.setCursor(Qt.PointingHandCursor)
        self._send_btn.clicked.connect(self._submit)
        input_row = QHBoxLayout()
        input_row.setContentsMargins(16, 8, 16, 0)
        input_row.setSpacing(8)
        input_row.addWidget(self._edit, 1)
        input_row.addWidget(self._attach_btn, 0, Qt.AlignBottom)
        input_row.addWidget(self._send_btn)
        hint = QLabel("记录按天保存在 ~/.WisePet/chat/ · 只存在本机", card)
        hint.setObjectName("hint")

        body = QVBoxLayout(card)
        body.setContentsMargins(0, 0, 0, 12)
        body.setSpacing(0)
        body.addLayout(head)
        body.addLayout(status_row)
        body.addWidget(self._history, 1)
        body.addWidget(self._files)
        body.addLayout(input_row)
        body.addSpacing(8)
        body.addWidget(hint, 0, Qt.AlignCenter)

        QShortcut(QKeySequence(Qt.Key_Escape), self, self.hide)
        self._refresh_status()
        self._show_empty()

    # ---------- 对外 ----------

    def show_near(self, pet_rect) -> None:
        """贴在宠物左侧（左侧不够放右侧）；打开即可接着聊，并激活会话。"""
        self._pinned = False
        self._place(pet_rect)
        self.show()
        self.raise_()
        self._emit_active(True)
        self.reload()
        self.focus_input()

    def follow_pet(self, pet_rect, *, force: bool = False) -> None:
        """宠物挪窝后重新贴边；手动拖过或窗口关着时不动（FR v1.10 C18）。

        force=True（用户正在拖拽小猫）时无视 _pinned 强制跟随：对话框要贴着猫走。
        """
        if self.isVisible() and (force or not self._pinned):
            self._place(pet_rect)

    def focus_input(self) -> None:
        """点宠物时历史窗已经开着：把焦点给输入行，不再叠一条输入条。"""
        self.raise_()
        self.activateWindow()
        self._edit.setFocus()

    # ---------- 模型方案切换 ----------

    def set_models(self, names: list[str], active: str) -> None:
        """刷新模型下拉；信号 block 防止程序性填充被当成用户切换。"""
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        self._model_combo.addItems(names)
        if active in names:
            self._model_combo.setCurrentText(active)
        self._model_combo.blockSignals(False)

    # ---------- 附件 ----------

    def take_attachments(self) -> tuple[Attachment, ...]:
        """取走当前附件（发送后清空）。"""
        attachments, self._attachments = tuple(self._attachments), []
        self._sync_files_label()
        return attachments

    def add_attachment(self, att: Attachment) -> None:
        """程序化附加（截图问小墨等入口）。"""
        if len(self._attachments) < MAX_ATTACHMENTS:
            self._attachments.append(att)
            self._sync_files_label()

    def _add_clipboard_image(self, image) -> None:
        """Ctrl+V 贴截图：转成图片附件（文本粘贴走 QTextEdit 默认路径）。"""
        if len(self._attachments) >= MAX_ATTACHMENTS:
            self._files.setText(f"一次最多 {MAX_ATTACHMENTS} 个附件")
            self._files.show()
            return
        att = attachment_from_clipboard_image(image)
        if att is not None:
            self._attachments.append(att)
            self._sync_files_label()

    def _pick_files(self) -> None:
        paths, _filter = QFileDialog.getOpenFileNames(
            self, "附加文件（可多选）", "",
            "支持的文件（图片/文本）(*.png *.jpg *.jpeg *.gif *.webp *.bmp "
            "*.txt *.md *.py *.json *.csv *.log *.yaml *.yml *.toml);;所有文件 (*)")
        for path in paths:
            if len(self._attachments) >= MAX_ATTACHMENTS:
                self._files.setText(f"一次最多 {MAX_ATTACHMENTS} 个附件")
                self._files.show()
                return
            try:
                self._attachments.append(load_attachment(path))
            except ValueError as exc:
                self._files.setText(str(exc))
                self._files.show()
                return
        self._sync_files_label()

    def _sync_files_label(self) -> None:
        if not self._attachments:
            self._files.hide()
            self._files.clear()
            return
        self._files.setText("📎 " + " + ".join(att.name for att in self._attachments))
        self._files.show()

    def _place(self, pet_rect) -> None:
        if self._screen is not None:
            width = fit_beside_width(self._screen.geometry(), pet_rect, self.width(),
                                     _WINDOW_MIN_WIDTH)
            self.resize(width, self.height())
            x, y = self._screen.place_beside(pet_rect, self.width(), self.height())
        else:
            x, y = pet_rect.left() - self.width() - 12, pet_rect.center().y()
        self.move(x, y)

    def toggle(self, pet_rect) -> None:
        """右键「历史对话…」：开着就收起，关着就打开。"""
        if self.isVisible():
            self.hide()
        else:
            self.show_near(pet_rect)

    def reload(self) -> None:
        """重读日期列表并载入选中日的历史（启动 / 打开窗口时调用）。"""
        days = list(self._days_provider())
        today = day_key()
        if today not in days:
            days.append(today)
        days = sorted(set(days))
        self._day_combo.blockSignals(True)
        self._day_combo.clear()
        self._day_combo.addItems(days)
        self._day_combo.setCurrentText(today)
        self._day_combo.blockSignals(False)
        self._load_day(today)

    def load_history(self, records) -> None:
        """用一批记录替换历史区（启动恢复 / 切天时用）。"""
        self._history.clear()
        self._empty_hint = False
        self._count = 0
        for record in records:
            self.append_record(record)
        if not self._count:
            self._show_empty()

    def append_record(self, record) -> None:
        """追加一条完整对话（不是流式增量）。"""
        if self._empty_hint:
            self._history.clear()
            self._empty_hint = False
        self._history.append(_bubble_html(record))
        self._count += 1
        self._refresh_status()

    def receive_record(self, record) -> None:
        """新对话实时上屏：只有正在看「今天」时才插，停在历史日期时不串台。

        旧日期视图被切回当天时会 reload，重新从仓储读全量，不会丢消息。
        """
        if self.session_day == day_key():
            self.append_record(record)

    def set_state(self, phase: str, source: str = "", detail: str = "") -> None:
        """会话阶段 + 大脑来源：调用大模型时能看到现在处于哪一步。"""
        self._phase = phase
        if source:
            self._brain = source
        self._detail = detail
        self._refresh_status()

    @property
    def status_text(self) -> str:
        return self._status.text()

    @property
    def session_day(self) -> str:
        return self._day_combo.currentText()

    @property
    def day_combo(self) -> QComboBox:
        return self._day_combo

    @property
    def close_button(self) -> QPushButton:
        return self._close_btn

    @property
    def line_edit(self) -> ChatTextEdit:
        """测试注入文本 / 组装根聚焦用。"""
        return self._edit

    @property
    def send_button(self) -> QPushButton:
        return self._send_btn

    # ---------- 内部 ----------

    def _submit(self) -> None:
        text = self._edit.text().strip()
        if not text and not self._attachments:
            self._edit.clear()          # 空消息直接丢弃（PRD M4 边界）
            return
        self.submitted.emit(text)
        self._edit.clear()
        self.take_attachments()
        self._edit.setFocus()           # 回复期间可继续输入

    def _show_empty(self) -> None:
        self._empty_hint = True
        self._history.setHtml(
            '<p align="center" style="color:#B09A7C;font-size:12px;">'
            '这天还没有聊天记录～<br>在下面输入一句就能和我聊聊</p>')

    def _refresh_status(self) -> None:
        phase = _PHASE_TEXT.get(self._phase, self._phase)
        if self._phase == PHASE_ERROR and self._detail:
            phase = f"{phase}（{self._detail}）"
        brain = _BRAIN_TEXT.get(self._brain, self._brain)
        self._status.setText(f"会话 {self.session_day or day_key()} · {self._count} 条"
                             f" ｜ {phase} ｜ 大脑：{brain}")

    def _load_day(self, day: str) -> None:
        if not day:
            return
        self.load_history(self._records_provider(day))
        self._refresh_status()

    def _on_day_changed(self, day: str) -> None:
        self._load_day(day)

    def _emit_active(self, active: bool) -> None:
        if active != self._active:
            self._active = active
            self.active_changed.emit(active)

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt 重写
        self._emit_active(False)
        super().hideEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt 重写
        if event.key() == Qt.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt 重写
        """标题栏/card 空白处转发拖动（输入行 Esc 由 ChatTextEdit.dismissed 处理）。"""
        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self._begin_drag(event.globalPosition().toPoint())
        elif event.type() == QEvent.MouseMove and self._drag_global is not None:
            self._move_by(event.globalPosition().toPoint())
        elif event.type() == QEvent.MouseButtonRelease:
            self._end_drag()
        return super().eventFilter(obj, event)

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
        self._pinned = True             # 用户自己放的位置优先于自动跟随

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 重写
        """无边框窗口：按住空白处可拖动（控件自己会吃掉事件，不影响日期/滚动）。"""
        if event.button() == Qt.LeftButton:
            self._begin_drag(event.globalPosition().toPoint())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt 重写
        if event.buttons() & Qt.LeftButton:
            self._move_by(event.globalPosition().toPoint())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 重写
        self._end_drag()
        super().mouseReleaseEvent(event)
