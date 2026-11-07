"""写盘类意图的「预览 → 确认」对话框：把风险等级、目标路径与将写入内容摊开。

替代原来的 QMessageBox：QMessageBox 只能给一句标题 + 折叠详情，
放不下权限级别和「本类动作以后都允许」这类需要用户做长期授权的选择。
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QVBoxLayout,
)

_STYLE = """
QDialog { background: #FFFDF7; }
QLabel#confirmHeadline { color: #3A2B21; font-size: 14px; font-weight: 600; }
QLabel#confirmRisk { color: #8A4A4A; font-size: 11px; }
QPlainTextEdit {
    background: #FFFFFF; border: 1px solid #E7D3B2; border-radius: 9px;
    padding: 8px; color: #3A2B21; font-size: 12px;
}
QCheckBox { color: #6B4F3A; font-size: 12px; }
QDialogButtonBox QPushButton {
    background: #FFFFFF; border: 1px solid #E2CFAF; border-radius: 9px;
    color: #6B4F3A; font-size: 12px; padding: 6px 16px;
}
QDialogButtonBox QPushButton:hover { background: #F7E7CC; }
"""

_RISK_TEXT = "此操作会创建或覆盖本机文件，请确认路径与内容；执行后可在右键菜单「撤销上一次写文件」还原。"


class ActionConfirmDialog(QDialog):
    """返回 (是否执行, 是否记住本类动作以后都允许)。"""

    def __init__(self, headline: str, preview: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("WisePet 想执行一个操作")
        self.setStyleSheet(_STYLE)
        self.setMinimumWidth(520)
        self._remembered = False

        title = QLabel(headline, self)
        title.setObjectName("confirmHeadline")
        risk = QLabel(_RISK_TEXT, self)
        risk.setObjectName("confirmRisk")
        risk.setWordWrap(True)
        detail = QPlainTextEdit(preview, self)
        detail.setReadOnly(True)
        detail.setMinimumHeight(200)
        self._remember = QCheckBox("信任写盘类操作，以后不再逐次确认", self)
        self._remember.setToolTip("会把设置里的写盘权限改为 auto；随时可在设置窗改回")
        buttons = QDialogButtonBox(self)
        self._yes = buttons.addButton("执行", QDialogButtonBox.AcceptRole)
        buttons.addButton("先不写", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.addWidget(title)
        layout.addWidget(risk)
        layout.addWidget(detail, 1)
        layout.addWidget(self._remember)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        self._remembered = self._remember.isChecked()
        self.accept()

    def set_remember_enabled(self, enabled: bool) -> None:
        """设置里关掉「记住授权」时，确认框不该偷偷提供长期放行的选项。"""
        self._remember.setEnabled(enabled)
        if not enabled:
            self._remember.setChecked(False)

    def result_value(self) -> tuple[bool, bool]:
        return self.result() == QDialog.Accepted, self._remembered

    @classmethod
    def decide(cls, approved: bool, remembered: bool) -> str:
        """把用户选择翻译成权限动作（纯函数，便于不弹窗也能测策略）。"""
        if not approved:
            return "deny_once"
        return "auto" if remembered else "confirm"
