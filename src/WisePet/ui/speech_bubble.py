"""气泡窗口：多卡片通知堆叠 + 流式增量节流 + 提醒「知道啦」。

每条通知是一张独立小卡（标题 + 正文 + 可选「知道啦」），多条同时出现时垂直
堆叠、按标题区分，关一条不影响其它；提醒类 15s 未确认自动消失。
AI 流式回复也是一张卡：begin_stream 建卡、append_chunk 节流刷新、end_stream 定时收场。
正文完整渲染 markdown（标题/加粗/代码块/表格/链接等，mistune 转义防注入）；
右下角尺寸手柄可拖拽调整宽度（用户改过之后自动布局不再覆盖宽度）。
"""
from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import QRect, QSize, QTimer, QPoint, Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from .markdown_render import render_markdown
from .resize_grip import ResizeGrip
from .screen_bounds import ScreenBounds

logger = logging.getLogger(__name__)

#: 卡片外观尺寸
_BUBBLE_PAD = 12
_CARD_WIDTH = 320            # 默认宽度
_CARD_MIN_WIDTH = 240        # 用户可往窄里拖的下限
#: 卡片底部与宠物头顶锚点之间的留白（替代原来的三角尾巴）
_ANCHOR_GAP = 12

#: 流式刷新节流：≥80ms 合并一次，避免频繁 repaint
CHUNK_THROTTLE_MS = 80

#: 各来源的驻留时长（毫秒）：提醒需要确认，但 15s 不点就自动走，不长期压屏
DURATION_MS_BY_SOURCE: dict[str, int] = {
    "ai": 12_000,
    "reminder": 15_000,   # 未点「知道啦」15s 自动消失
    "news": 25_000,       # 新闻速览比较长，多留阅读时间
    "cheer": 15_000,      # 加油打气
    "help": 10_000,
    "fallback": 6_000,
    "system": 5_000,
}
DEFAULT_DURATION_MS = 5_000
#: 短文本至少驻留 3s，长文本按字数追加
_MIN_DURATION_MS = 3_000
_PER_CHAR_MS = 50

#: 来源 → 默认标题（main 层可传入更具体的标题覆盖）
_TITLE_BY_SOURCE = {
    "reminder": "提醒",
    "news": "新闻速览",
    "cheer": "加油打气",
    "help": "求助",
    "fallback": "出错了",
    "ai": "小墨",
    "pet": "小墨",
    "system": "小墨",
}

#: 与输入条 / 历史窗同一套配色，不引入渐变、阴影或贴图
_STYLE = """
QFrame#bubbleCard {
    background: #FFFDF7;
    border: 1px solid #DFC49A;
    border-radius: 14px;
}
QLabel#bubbleDot { color: #E8A45C; font-size: 13px; background: transparent; }
QLabel#bubbleTitle { color: #5A3E28; font-size: 12px; font-weight: 600; background: transparent; }
QLabel#bubbleText { color: #3A2B21; font-size: 12px; background: transparent; }
QPushButton#ack {
    background: #F8D79B; border: 1px solid #E7C79A; border-radius: 9px;
    color: #6B4F3A; font-size: 11px; padding: 3px 12px;
}
QPushButton#ack:hover { background: #F3D9A8; }
"""

#: 轻量 markdown 转换已被 ui/markdown_render.py 的完整渲染取代（mistune）。


class _Card:
    """一张通知卡：标题 + 正文 + 可选「知道啦」+ 独立自动消失计时。"""

    def __init__(self, card_id: int, parent: QWidget, title: str, text: str,
                 *, with_ack: bool, on_ack: Callable[[], None] | None,
                 on_close: Callable[[], None] | None, duration_ms: int):
        self.id = card_id
        self.on_ack = on_ack
        self.on_close = on_close

        self.frame = QFrame(parent)
        self.frame.setObjectName("bubbleCard")
        self.frame.setMinimumWidth(_CARD_MIN_WIDTH)   # 宽度随窗口/内容伸缩

        self.title_label = QLabel(title, self.frame)
        self.title_label.setObjectName("bubbleTitle")
        self.dot = QLabel("●", self.frame)
        self.dot.setObjectName("bubbleDot")
        self.text_label = QLabel(self.frame)
        self.text_label.setObjectName("bubbleText")
        self.text_label.setWordWrap(True)
        self.text_label.setTextFormat(Qt.TextFormat.RichText)
        self.text_label.setOpenExternalLinks(True)   # markdown 链接可点击（外部浏览器）
        self.text_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.raw_text = text                    # 原文（markdown 未转换），current_text 用
        self.text_label.setText(render_markdown(text))

        self.ack_btn = QPushButton("知道啦", self.frame)
        self.ack_btn.setObjectName("ack")
        self.ack_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ack_btn.setVisible(with_ack)

        head = QHBoxLayout()
        head.setSpacing(6)
        head.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignTop)
        head.addWidget(self.title_label, 1)
        body = QVBoxLayout(self.frame)
        body.setContentsMargins(_BUBBLE_PAD, _BUBBLE_PAD, _BUBBLE_PAD, _BUBBLE_PAD)
        body.setSpacing(6)
        body.addLayout(head)
        body.addWidget(self.text_label)
        if with_ack:
            row = QHBoxLayout()
            row.addStretch(1)
            row.addWidget(self.ack_btn)
            body.addLayout(row)

        self.hide_timer = QTimer(parent)
        self.hide_timer.setSingleShot(True)
        if duration_ms > 0:
            self.hide_timer.timeout.connect(lambda: parent.close_card(self.id))
            self.hide_timer.start(duration_ms)   # 不 start 就永不消失（真机截图踩过）


class SpeechBubble(QWidget):
    """独立置顶通知窗：多卡片垂直堆叠，可流式刷新其中一张。"""

    def __init__(self, screen_bounds: ScreenBounds | None = None,
                 parent: QWidget | None = None,
                 duration_provider: Callable[[], float] | None = None,
                 avoid_provider: Callable[[], QRect] | None = None):
        super().__init__(parent)
        self._screen = screen_bounds
        # 统一驻留时长（秒）：设置里配一个值管所有提示；不注入则按来源内置时长
        self._duration_provider = duration_provider
        #: 宠物窗口矩形提供方：气泡默认放头顶，放不下时挪到宠物侧面不挡它
        self._avoid_provider = avoid_provider
        self._anchor = QPoint()
        self._cards: dict[int, _Card] = {}
        self._order: list[int] = []            # 卡片显示顺序（旧→新）
        self._seq = 0
        self._stream_id: int | None = None
        self._stream_buffer: list[str] = []
        self._stream_on_close: Callable[[], None] | None = None
        self._user_resized = False      # 用户拖过手柄后，自动布局不再覆盖宽度

        # 右下角尺寸手柄：无边框窗口没有系统边框，拖它调整大小
        self._grip = ResizeGrip(self)
        self._grip.min_size = QSize(_CARD_MIN_WIDTH, 48)
        self._grip.user_resized.connect(lambda: setattr(self, "_user_resized", True))

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(_STYLE)

        self._stack = QVBoxLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.setSpacing(8)

        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(CHUNK_THROTTLE_MS)
        self._flush_timer.timeout.connect(self._flush_stream)

    # ---------- 一次性通知 ----------

    def show_text(self, text: str, *, source: str = "system",
                  anchor: QPoint | None = None, title: str = "",
                  with_ack: bool = False,
                  on_ack: Callable[[], None] | None = None,
                  on_close: Callable[[], None] | None = None) -> None:
        """追加一条通知卡（不覆盖已有卡片，多条并存、按标题区分）。"""
        self._add_card(title or _TITLE_BY_SOURCE.get(source, "小墨"), text,
                       with_ack=with_ack, on_ack=on_ack, on_close=on_close,
                       duration_ms=self._duration_ms(text, source))
        if anchor is not None:
            self._anchor = anchor
        self._place_and_show()

    # ---------- AI 流式回复（单卡复用） ----------

    def begin_stream(self, anchor: QPoint | None = None, title: str = "",
                     on_close: Callable[[], None] | None = None) -> None:
        """流式回复开始：关掉上一张流卡，建一张空卡避免首包前无反馈。

        兜底计时 = max(60s, 配置时长×2)：即使 end_stream 因异常路径丢失，
        这张卡也不会永远挂在屏幕上（真机截图踩过）。
        """
        if self._stream_id is not None:
            self.close_card(self._stream_id)
        self._stream_buffer = []
        self._stream_on_close = on_close
        self._stream_id = self._add_card(
            title or _TITLE_BY_SOURCE.get("ai", "小墨"), "…",
            with_ack=False, on_ack=None, on_close=on_close,
            duration_ms=max(60_000, self._duration_ms("", "ai") * 2))
        if anchor is not None:
            self._anchor = anchor
        self._place_and_show()

    def append_chunk(self, delta: str) -> None:
        """收到增量文本：进缓冲区，节流到下一次 flush。"""
        if not delta:
            return
        self._stream_buffer.append(delta)
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def end_stream(self, full_text: str = "", anchor: QPoint | None = None,
                   on_close: Callable[[], None] | None = None) -> None:
        """流结束：立刻刷新全部内容并安排自动消失。"""
        self._flush_timer.stop()
        card = self._cards.get(self._stream_id) if self._stream_id else None
        if card is None:                       # 流卡被提前关掉：补一张完整卡
            self._stream_id = self._add_card(
                _TITLE_BY_SOURCE.get("ai", "小墨"), "", with_ack=False,
                on_ack=None, on_close=on_close, duration_ms=0)
            card = self._cards[self._stream_id]
        if full_text:
            self._stream_buffer = [full_text]
        self._set_card_text(card, self._current_text().strip())
        if anchor is not None:
            self._anchor = anchor
        if on_close is not None:
            card.on_close = on_close
            self._stream_on_close = on_close
        card.hide_timer.start(self._duration_ms(self._current_text(), "ai"))
        self._place_and_show()

    # ---------- 查询 / 移动 ----------

    def follow(self, anchor: QPoint) -> None:
        """跟着宠物走：只改锚点并重算位置（宠物走动/拖拽时气泡不脱离）。"""
        self._anchor = anchor
        if self.isVisible() and self._cards:
            self._place_and_show()

    def current_text(self) -> str:
        """最新一张卡的正文原文（未转 HTML；测试与调试用）。"""
        if not self._order:
            return ""
        return self._cards[self._order[-1]].raw_text

    @property
    def cards(self) -> list[_Card]:
        """按显示顺序返回当前卡片（测试用）。"""
        return [self._cards[cid] for cid in self._order]

    def hide_bubble(self) -> None:
        """关闭全部卡片并逐卡通知驱动方（宠物隐藏等场景）。"""
        for card_id in list(self._order):
            self.close_card(card_id)

    # ---------- 内部：卡片生命周期 ----------

    def _add_card(self, title: str, text: str, *, with_ack: bool,
                  on_ack: Callable[[], None] | None,
                  on_close: Callable[[], None] | None,
                  duration_ms: int) -> int:
        self._seq += 1
        card = _Card(self._seq, self, title, text, with_ack=with_ack,
                     on_ack=on_ack, on_close=on_close, duration_ms=duration_ms)
        card.ack_btn.clicked.connect(lambda: self._ack_clicked(card.id))
        self._cards[card.id] = card
        self._order.append(card.id)
        self._stack.addWidget(card.frame)
        return card.id

    def _ack_clicked(self, card_id: int) -> None:
        card = self._cards.get(card_id)
        if card is None:
            return
        self.close_card(card_id)               # 先收场（驱动方回待机），再上报确认
        if card.on_ack is not None:
            card.on_ack()

    def close_card(self, card_id: int) -> None:
        """关一张卡：通知其 on_close（一次性），最后一个卡关掉时收起整个气泡窗。"""
        card = self._cards.pop(card_id, None)
        if card is None:
            return
        card.hide_timer.stop()
        if card.id in self._order:
            self._order.remove(card.id)
        if self._stream_id == card_id:
            self._stream_id = None
        self._stack.removeWidget(card.frame)
        card.frame.deleteLater()
        if card.on_close is not None:
            card.on_close()
        if not self._cards:
            self.hide()

    def _set_card_text(self, card: _Card, text: str) -> None:
        card.raw_text = text
        card.text_label.setText(render_markdown(text))

    # ---------- 内部：流式与布局 ----------

    def _flush_stream(self) -> None:
        card = self._cards.get(self._stream_id) if self._stream_id else None
        if card is not None:
            self._set_card_text(card, self._current_text().strip())
            self._place_and_show()

    def _current_text(self) -> str:
        return "".join(self._stream_buffer)

    def _duration_ms(self, text: str, source: str) -> int:
        """驻留时长：设置了统一时长就管所有提示；否则按来源内置时长。"""
        if self._duration_provider is not None:
            try:
                return max(3_000, int(float(self._duration_provider()) * 1000))
            except (TypeError, ValueError):
                pass
        base = DURATION_MS_BY_SOURCE.get(source, DEFAULT_DURATION_MS)
        if source in ("reminder", "news", "help"):
            return base
        by_length = _MIN_DURATION_MS + _PER_CHAR_MS * len(text)
        return max(_MIN_DURATION_MS, min(base, by_length))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 重写
        """尺寸手柄贴右下角并置顶（卡片是后加的子控件，不置顶会盖住手柄）。"""
        self._grip.move(self.width() - 16, self.height() - 16)
        self._grip.raise_()
        super().resizeEvent(event)

    def _place_and_show(self) -> None:
        """堆叠所有卡片并定位（先让布局算好尺寸再摆）。

        宽度：默认 _CARD_WIDTH；用户拖过手柄后尊重用户宽度（不小于下限）。
        高度：按宽度 heightForWidth 算（换行文本随宽度变化）。
        位置：首选宠物头顶居中；与宠物窗口重叠时改为贴到宠物左/右侧
        （居中对齐宠物高度）；两侧都放不下才退回头顶位置。
        """
        if self._user_resized and self.isVisible():
            width = max(self.width(), _CARD_MIN_WIDTH)
        else:
            width = _CARD_WIDTH
        layout = self.layout()
        if layout is not None and layout.hasHeightForWidth():
            height = max(self.heightForWidth(width), 48)
        else:
            height = max(self.sizeHint().height(), 48)
        self.resize(width, height)

        ax, ay = self._anchor.x(), self._anchor.y()
        avoid: QRect | None = None
        if self._avoid_provider is not None:
            try:
                avoid = self._avoid_provider()
            except Exception:  # noqa: BLE001 - 几何回调异常不挡气泡显示
                logger.exception("avoid provider failed")
                avoid = None
        if avoid is not None and (avoid.width() <= 0 or avoid.height() <= 0):
            avoid = None

        # 候选位：头顶居中 → 宠物右侧 → 宠物左侧（与宠物高度居中对齐）
        candidates = [(ax - width // 2, ay - height - _ANCHOR_GAP)]
        if avoid is not None:
            side_y = avoid.y() + avoid.height() // 2 - height // 2
            candidates.append((avoid.x() + avoid.width() + 10, side_y))
            candidates.append((avoid.x() - width - 10, side_y))

        chosen: tuple[int, int] | None = None
        for x, y in candidates:
            cx, cy = x, y
            if self._screen is not None:
                cx, cy = self._screen.clamp(cx, cy, width, height)
            rect = QRect(cx, cy, width, height)
            if avoid is None or not rect.intersects(avoid.adjusted(-6, -6, 6, 6)):
                chosen = (cx, cy)
                break
        if chosen is None:                     # 两侧都放不下：退回头顶首选位
            x, y = candidates[0]
            if self._screen is not None:
                x, y = self._screen.clamp(x, y, width, height)
            chosen = (x, y)
        self.move(*chosen)
        self.show()
        self.raise_()
