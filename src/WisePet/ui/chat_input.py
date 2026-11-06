"""聊天输入条：点击宠物后浮现的输入框（PRD US-3 / FR v1.9 C15）。

回车发送、ESC 关闭；多行输入自动换行自适应高度；支持 📎 附加图片/文本文件，
附件随消息一并提交（图片走多模态，文本进上下文）。
空白消息直接丢弃，不往队列里塞空包（PRD M4 边界 / 不变量 C2）。
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QPushButton,
    QVBoxLayout, QWidget,
)

from ..domain.attachment import MAX_ATTACHMENTS, Attachment, load_attachment
from .chat_text_edit import ChatTextEdit, attachment_from_clipboard_image
from .screen_bounds import ScreenBounds

logger = logging.getLogger(__name__)

_INPUT_WIDTH = 400
_PLACEHOLDER = "和我说点什么…（回车发送，Shift+回车换行）"

_PHASE_TEXT = {
    "idle": "随时可以聊",
    "thinking": "思考中…",
    "replying": "正在回复…",
    "done": "回复完成",
    "error": "出错了",
}
_BRAIN_TEXT = {"ai": "大模型", "local": "本地小脑", "": "待确定"}

_STYLE = """
QFrame#inputCard {
    background: #FFFDF7;
    border: 1px solid #DFC49A;
    border-radius: 14px;
}
QLabel#status { color: #9A7A55; font-size: 10px; padding: 0 2px; }
QLabel#files { color: #B0885C; font-size: 10px; }
QTextEdit {
    background: #FFFFFF; border: 1px solid #E7D3B2; border-radius: 9px;
    padding: 5px 9px; color: #3A2B21; font-size: 12px;
    selection-background-color: #F3D9A8;
}
QTextEdit:focus { border: 1px solid #E8A45C; }
QPushButton#attach {
    background: transparent; border: none; color: #A08A6C;
    font-size: 15px; min-width: 26px;
}
QPushButton#attach:hover { color: #6B4F3A; }
QComboBox {
    background: #FFFFFF; border: 1px solid #E2CFAF; border-radius: 8px;
    padding: 1px 6px; color: #6B4F3A; font-size: 10px;
}
QComboBox::drop-down { border: 0; width: 14px; }
"""


class ChatInput(QWidget):
    """贴附在宠物上方的输入条（圆角小卡片 + 状态标签 + 📎）。"""

    #: 非空文本 → 主程序转交 ChatService
    submitted = Signal(str)
    #: ESC 关闭输入条
    dismissed = Signal()
    #: 输入条打开/关闭 → 组装根据此冻结/恢复宠物位移
    active_changed = Signal(bool)
    #: 用户在下拉里切了模型方案 → 主程序热切换 AI 配置
    model_selected = Signal(str)

    def __init__(self, screen_bounds: ScreenBounds | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._screen = screen_bounds
        self._active = False
        self._phase = "idle"
        self._brain = ""
        self._detail = ""
        self._history_count = 0
        self._attachments: list[Attachment] = []

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(_INPUT_WIDTH)
        self.setStyleSheet(_STYLE)

        card = QFrame(self)
        card.setObjectName("inputCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

        self._status = QLabel(card)
        self._status.setObjectName("status")
        self._files = QLabel(card)
        self._files.setObjectName("files")
        self._files.setWordWrap(True)
        self._files.hide()

        self._edit = ChatTextEdit(card)
        self._edit.setPlaceholderText(_PLACEHOLDER)
        self._edit.send_requested.connect(self._submit)
        self._edit.dismissed.connect(self._on_dismissed)
        self._edit.image_pasted.connect(self._add_clipboard_image)

        self._attach_btn = QPushButtonAttach(card)
        self._attach_btn.setObjectName("attach")
        self._attach_btn.setText("📎")
        self._attach_btn.setFixedSize(28, 28)
        self._attach_btn.setToolTip("附加图片或文本文件（图片需视觉模型）")
        self._attach_btn.setCursor(Qt.PointingHandCursor)
        self._attach_btn.clicked.connect(self._pick_files)

        self._model_combo = QComboBox(card)
        self._model_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents)   # 宽度跟最长的方案名走
        self._model_combo.setToolTip("切换 AI 模型方案（右键→设置里可增删方案）")
        self._model_combo.currentTextChanged.connect(
            lambda name: self.model_selected.emit(name))
        model_label = QLabel("模型", card)
        model_label.setObjectName("status")
        status_row = QHBoxLayout()
        status_row.setContentsMargins(12, 9, 12, 0)
        status_row.addWidget(self._status, 1)
        status_row.addWidget(model_label, 0)
        status_row.addWidget(self._model_combo, 0)
        files_row = QHBoxLayout()
        files_row.setContentsMargins(12, 2, 12, 0)
        files_row.addWidget(self._files, 1)
        input_row = QHBoxLayout()
        input_row.setContentsMargins(9, 5, 9, 9)
        input_row.setSpacing(4)
        input_row.addWidget(self._edit, 1)
        input_row.addWidget(self._attach_btn, 0, Qt.AlignmentFlag.AlignBottom)

        body = QVBoxLayout(card)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addLayout(status_row)
        body.addLayout(files_row)
        body.addLayout(input_row)
        self._refresh_status()

    # ---------- 对外 ----------

    def show_near(self, pet_rect) -> None:
        """贴在宠物左侧（左侧不够放右侧）；传入宠物全局矩形而非锚点，避免压住宠物。"""
        self._place(pet_rect)
        self.show()
        self.raise_()
        self.activateWindow()
        self._edit.setFocus()
        self._emit_active(True)

    def follow_pet(self, pet_rect) -> None:
        """宠物挪窝后重新贴边：只改坐标，不抢焦点也不重发激活信号（FR v1.10 C18）。"""
        if self.isVisible():
            self._place(pet_rect)

    def _place(self, pet_rect) -> None:
        self.adjustSize()
        if self._screen is not None:
            x, y = self._screen.place_beside(pet_rect, self.width(), self.height())
        else:
            x, y = pet_rect.left() - self.width() - 12, pet_rect.center().y()
        self.move(x, y)

    def set_history_count(self, count: int) -> None:
        """状态标签带上一句「已记 N 条」，让保存是否生效一眼可见。"""
        self._history_count = max(0, int(count))
        self._refresh_status()

    @property
    def history_count(self) -> int:
        return self._history_count

    def set_state(self, phase: str, source: str = "", detail: str = "") -> None:
        """会话阶段 + 大脑来源：调用大模型时也能看到现在处于哪一步。"""
        self._phase = phase
        if source:
            self._brain = source
        self._detail = detail
        self._refresh_status()

    @property
    def status_text(self) -> str:
        return self._status.text()

    @property
    def line_edit(self) -> ChatTextEdit:
        """测试注入文本 / 组装根聚焦用。"""
        return self._edit

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

    def _add_clipboard_image(self, image) -> None:
        """Ctrl+V 贴截图：转成图片附件（文本粘贴走 QTextEdit 默认路径）。"""
        if len(self._attachments) >= MAX_ATTACHMENTS:
            self._files.setText(f"一次最多 {MAX_ATTACHMENTS} 个附件")
            self._files.show()
            return
        att = attachment_from_clipboard_image(image)
        if att is None:
            return
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
                logger.debug("attachment rejected: %s", exc)
                self._files.setText(str(exc))
                self._files.show()
                return
        self._sync_files_label()

    def _sync_files_label(self) -> None:
        if not self._attachments:
            self._files.hide()
            self._files.clear()
            return
        names = " + ".join(att.name for att in self._attachments)
        self._files.setText(f"📎 {names}")
        self._files.show()
        self.adjustSize()

    # ---------- 内部 ----------

    def _on_dismissed(self) -> None:
        self.hide()
        self.dismissed.emit()

    def _refresh_status(self) -> None:
        phase = _PHASE_TEXT.get(self._phase, self._phase)
        if self._phase == "error" and self._detail:
            phase = f"{phase}（{self._detail}）"
        brain = _BRAIN_TEXT.get(self._brain, self._brain)
        # 文案保持一行放得下（右侧还有「模型」标签 + 下拉），提示语进 tooltip
        suffix = f"  ·  已记 {self._history_count} 条" if self._history_count else ""
        self._status.setText(f"●  {phase}  ·  {brain}{suffix}")
        self._status.setToolTip("聊天记录按天保存在 ~/.WisePet/chat/；右键宠物 →「历史对话…」回看")

    def _emit_active(self, active: bool) -> None:
        if active != self._active:
            self._active = active
            self.active_changed.emit(active)

    def _submit(self) -> None:
        text = self._edit.text().strip()
        if not text and not self._attachments:
            self._edit.clear()      # 空消息直接丢弃（PRD M4 边界）
            return
        self.submitted.emit(text)
        self._edit.clear()
        self.take_attachments()
        self._edit.setFocus()       # 回复期间可继续输入

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt 重写
        self._emit_active(False)
        super().hideEvent(event)


class QPushButtonAttach(QPushButton):
    """📎 按钮（独立小类只为 setObjectName 的 QSS 选择器稳定）。"""
