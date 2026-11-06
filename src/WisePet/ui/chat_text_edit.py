"""聊天多行输入框：自动换行 + 随内容自适应高度（回车发送，Shift+回车换行）。

替代单行 QLineEdit：长文本按控件宽度自动折行，行数多了往上长（封顶 _MAX_LINES），
不出现横向滚动条。text()/setText() 与 QLineEdit 同名兼容，存量测试与调用方无感。
"""
from __future__ import annotations

from PySide6.QtCore import QBuffer, QIODevice, Qt, Signal
from PySide6.QtGui import QGuiApplication, QImage, QTextOption
from PySide6.QtWidgets import QTextEdit

from ..domain.attachment import attachment_from_png_bytes

#: 最多长到几行，再往后内部滚动
_MAX_LINES = 6
_LINE_HEIGHT_PAD = 4


def attachment_from_clipboard_image(image: QImage):
    """剪贴板 QImage → PNG 附件；空图/超限返回 None（调用方提示用户）。"""
    if image is None or image.isNull():
        return None
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    try:
        return attachment_from_png_bytes(bytes(buffer.data()))
    except ValueError:
        return None


class ChatTextEdit(QTextEdit):
    """Enter 发送 / Shift+Enter 换行的自增高多行输入框；Ctrl+V 可直接贴截图。"""

    #: 用户按了回车 → 外部执行发送
    send_requested = Signal()
    #: 用户按了 Esc → 外部决定关闭哪个窗
    dismissed = Signal()
    #: 剪贴板里是图片时按下 Ctrl+V → 外部转成附件（文本粘贴走默认路径）
    image_pasted = Signal(QImage)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAnywhere)   # 中英混排不出现横向滚动
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setTabChangesFocus(True)                             # Tab 别往正文里塞制表符
        self.document().documentLayout().documentSizeChanged.connect(
            self._adjust_height)
        self.setFixedHeight(self._height_for_lines(1))

    # ---------- QLineEdit 同名兼容 ----------

    def text(self) -> str:
        return self.toPlainText()

    def setText(self, text: str) -> None:  # noqa: N802 - 兼容 QLineEdit 命名
        self.setPlainText(text)

    # ---------- Qt 重写 ----------

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt 重写
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) \
                and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.send_requested.emit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.dismissed.emit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_V \
                and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            clipboard = QGuiApplication.clipboard()
            image = clipboard.image()
            if image is not None and not image.isNull():
                self.image_pasted.emit(image)
                event.accept()
                return
        super().keyPressEvent(event)

    # ---------- 内部 ----------

    def _height_for_lines(self, lines: int) -> int:
        metrics = self.fontMetrics()
        return min(_MAX_LINES, max(1, lines)) * metrics.height() \
            + 2 * 6 + _LINE_HEIGHT_PAD            # 文档边距 + 余量

    def _adjust_height(self, _size) -> None:
        """内容变多就长高，变少就缩回；封顶后交给内部滚动条。

        document().size().height() 是「像素」不是行数——直接 int() 当行数会让
        空框也顶到最高（真机截图踩过）；这里按行高换算并扣掉文档边距。
        """
        doc = self.document()
        line_height = max(1, self.fontMetrics().height())
        usable = max(0.0, doc.size().height() - 2 * doc.documentMargin())
        line_count = max(1, round(usable / line_height))
        target = self._height_for_lines(min(line_count, _MAX_LINES))
        if target != self.height():
            self.setFixedHeight(target)
