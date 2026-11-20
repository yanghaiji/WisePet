"""SpeechBubble 冒烟：多卡通知堆叠、标题区分、流式节流、提醒确认与自动消失。"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QFrame

from WisePet.ui.speech_bubble import (
    CHUNK_THROTTLE_MS,
    DURATION_MS_BY_SOURCE,
    SpeechBubble,
)


@pytest.fixture
def bubble(qtbot, fake_screen):
    widget = SpeechBubble(fake_screen)
    qtbot.addWidget(widget)
    return widget


def should_show_text_and_ack_button_when_reminder(bubble):
    bubble.show_text("该喝水啦", source="reminder", anchor=QPoint(400, 300),
                     title="喝水提醒", with_ack=True, on_ack=lambda: None)

    assert bubble.current_text() == "该喝水啦"
    assert bubble.cards[-1].ack_btn.isVisibleTo(bubble)
    assert bubble.cards[-1].title_label.text() == "喝水提醒"


def should_ack_and_notify_close_when_know_button_clicked(bubble, qtbot):
    events = []
    bubble.show_text("该喝水啦", source="reminder", anchor=QPoint(400, 300),
                     with_ack=True,
                     on_ack=lambda: events.append("ack"),
                     on_close=lambda: events.append("close"))
    bubble.show()
    qtbot.mouseClick(bubble.cards[-1].ack_btn, Qt.LeftButton)

    assert events == ["close", "ack"]     # 先关气泡告知驱动方，再上报确认
    assert not bubble.isVisible()


def should_hide_ack_button_when_plain_text_shown(bubble):
    bubble.show_text("随便说说", anchor=QPoint(400, 300))

    assert not bubble.cards[-1].ack_btn.isVisibleTo(bubble)


class TestMultiCard:
    """多条通知并存：各自带标题、关一张不影响其它、全关才收起窗口。"""

    def should_stack_cards_with_distinct_titles(self, bubble):
        bubble.show_text("该喝水啦", source="reminder", anchor=QPoint(400, 300),
                         title="喝水提醒", with_ack=True)
        bubble.show_text("AI 摘要新闻", source="news", anchor=QPoint(400, 300),
                         title="新闻速览")

        assert len(bubble.cards) == 2
        titles = [c.title_label.text() for c in bubble.cards]
        assert titles == ["喝水提醒", "新闻速览"]
        assert bubble.current_text() == "AI 摘要新闻"     # 最新一条
        assert bubble.isVisible()

    def should_close_card_via_close_button(self, bubble, qtbot):
        """每张卡右上角 × 可随时关（视频完成卡不用干等 20 秒）。"""
        bubble.show_text("视频做好啦", source="ai", anchor=QPoint(400, 300))
        card = bubble.cards[-1]
        assert card.close_btn.isVisible()
        card.close_btn.click()
        assert card not in bubble.cards
        assert not bubble.isVisible()          # 唯一一张关掉 → 窗口收起

    def should_close_only_clicked_card(self, bubble, qtbot):
        closed = []
        bubble.show_text("第一条", anchor=QPoint(400, 300), with_ack=True,
                         on_close=lambda: closed.append(1))
        bubble.show_text("第二条", anchor=QPoint(400, 300), with_ack=True,
                         on_close=lambda: closed.append(2))

        qtbot.mouseClick(bubble.cards[0].ack_btn, Qt.LeftButton)

        assert closed == [1]
        assert len(bubble.cards) == 1
        assert bubble.current_text() == "第二条"
        assert bubble.isVisible()                          # 还剩一张：窗口不收

    def should_hide_window_only_when_last_card_closed(self, bubble, qtbot):
        bubble.show_text("只有一张", anchor=QPoint(400, 300), with_ack=True)
        bubble.show()
        qtbot.mouseClick(bubble.cards[-1].ack_btn, Qt.LeftButton)

        assert not bubble.isVisible()

    def should_close_all_with_callbacks_when_hide_bubble(self, bubble):
        closed = []
        bubble.show_text("一", anchor=QPoint(400, 300), on_close=lambda: closed.append(1))
        bubble.show_text("二", anchor=QPoint(400, 300), on_close=lambda: closed.append(2))
        bubble.hide_bubble()
        bubble.hide_bubble()                               # 重复关闭不重复回调

        assert sorted(closed) == [1, 2]
        assert not bubble.isVisible() and bubble.cards == []


def should_auto_dismiss_card_when_duration_expires(bubble, qtbot):
    """到期自动消失：计时器必须真的被 start（真机踩过：只连接没启动，卡片堆屏）。"""
    closed = []
    bubble.show_text("会自己走的", anchor=QPoint(400, 300),
                     on_close=lambda: closed.append(1))
    bubble.cards[-1].hide_timer.start(50)              # 把 5s 驻留缩成 50ms

    qtbot.wait(300)

    assert closed == [1]
    assert bubble.cards == [] and not bubble.isVisible()


def should_default_title_by_source_when_not_given(bubble):
    bubble.show_text("标题测试", source="news", anchor=QPoint(400, 300))
    assert bubble.cards[-1].title_label.text() == "新闻速览"


def should_accumulate_chunks_when_stream_throttled(bubble, qtbot):
    bubble.begin_stream(QPoint(400, 300))
    for piece in ("你", "好", "呀"):
        bubble.append_chunk(piece)
    qtbot.wait(CHUNK_THROTTLE_MS + 30)     # 等节流窗口刷新一次

    assert bubble.current_text() == "你好呀"
    assert "你好呀" in bubble.cards[-1].text_label.text()


def should_replace_with_full_reply_when_stream_done(bubble):
    bubble.begin_stream(QPoint(400, 300))
    bubble.append_chunk("partial")
    bubble.end_stream("完整回复", anchor=QPoint(400, 300))

    assert "完整回复" in bubble.cards[-1].text_label.text()
    assert bubble.isVisible()


def should_show_generated_image_inline_when_stream_done(bubble, tmp_path):
    """画图产出要在气泡里内联显示，而不是只给一句「图存哪了」。"""
    from PySide6.QtGui import QColor, QImage
    img = tmp_path / "cat.png"
    canvas = QImage(4, 4, QImage.Format.Format_ARGB32)
    canvas.fill(QColor("#ff0000"))
    canvas.save(str(img))

    bubble.begin_stream(QPoint(400, 300))
    bubble.end_stream("画好啦", anchor=QPoint(400, 300), images=(str(img),))

    card = bubble.cards[-1]
    assert card.image_label.isVisible()
    assert not card.image_label.pixmap().isNull()
    assert card.save_btn.isVisible()             # 有图就有「保存图片」入口
    assert card._image_path == str(img)


def should_render_markdown_when_assistant_replies(bubble):
    """AI 回复里的 markdown 要真渲染（加粗/列表），原文仍是 current_text 口径。"""
    bubble.begin_stream(QPoint(400, 300))
    bubble.end_stream("**听音乐**\n\n- 甲\n- 乙", anchor=QPoint(400, 300))

    assert bubble.current_text() == "**听音乐**\n\n- 甲\n- 乙"
    html = bubble.cards[-1].text_label.text()
    assert "<strong>听音乐</strong>" in html
    assert "<li>" in html
    assert "**" not in html                       # 星号标记不能原样露出


def should_escape_raw_html_when_ai_outputs_it(bubble):
    """AI 输出的 HTML 一律转义显示，注入无门。"""
    bubble.begin_stream(QPoint(400, 300))
    bubble.end_stream("<script>alert(1)</script>", anchor=QPoint(400, 300))

    html = bubble.cards[-1].text_label.text()
    assert "&lt;script&gt;" in html and "<script>" not in html


def should_keep_user_width_when_grip_dragged(bubble, qtbot):
    """拖过右下手柄后：宽度记忆生效，流式刷新不覆盖用户的尺寸。"""
    bubble.begin_stream(QPoint(400, 300))
    bubble.append_chunk("内容")
    qtbot.wait(CHUNK_THROTTLE_MS + 30)
    bubble.resize(500, bubble.height())           # 模拟手柄拖拽
    bubble._grip.user_resized.emit()
    bubble.append_chunk("更多内容进来换行也会更长")
    qtbot.wait(CHUNK_THROTTLE_MS + 30)

    assert bubble.width() == 500                  # 自动布局没有抢回默认宽度
    assert bubble.cards[-1].raw_text.endswith("更长")


def should_follow_unified_duration_when_stream_done(fake_screen, qtbot):
    """AI 回复卡同样受「弹窗停留」配置约束：统一时长优先于内置表。"""
    bubble = SpeechBubble(fake_screen, duration_provider=lambda: 7)
    qtbot.addWidget(bubble)
    bubble.begin_stream(QPoint(400, 300))
    bubble.end_stream("完整回复", anchor=QPoint(400, 300))

    remaining = bubble.cards[-1].hide_timer.remainingTime()
    assert 6_500 <= remaining <= 7_000


def should_auto_dismiss_stream_card_when_end_stream_never_arrives(fake_screen, qtbot):
    """异常路径（如 AI 报错）拿不到 end_stream：兜底计时保证流卡也会消失。"""
    bubble = SpeechBubble(fake_screen, duration_provider=lambda: 15)
    qtbot.addWidget(bubble)
    bubble.begin_stream(QPoint(400, 300))          # 只建卡，永不调 end_stream
    bubble.cards[-1].hide_timer.start(50)

    qtbot.wait(300)

    assert bubble.cards == [] and not bubble.isVisible()


def should_use_durations_by_source(bubble):
    assert DURATION_MS_BY_SOURCE["reminder"] == 15_000   # 未确认 15s 自动消失
    assert bubble._duration_ms("喝水", "reminder") == 15_000
    assert bubble._duration_ms("短", "ai") >= 3_000
    assert bubble._duration_ms("长" * 400, "ai") <= DURATION_MS_BY_SOURCE["ai"]


def should_apply_unified_duration_when_provider_given(fake_screen, qtbot):
    """设置里配的「弹窗停留」管所有提示：统一时长优先于按来源的内置表。"""
    bubble = SpeechBubble(fake_screen, duration_provider=lambda: 7)
    qtbot.addWidget(bubble)

    assert bubble._duration_ms("该喝水啦", "reminder") == 7_000
    assert bubble._duration_ms("随便多长" * 100, "ai") == 7_000
    assert bubble._duration_ms("新闻", "news") == 7_000


def should_clamp_unified_duration_below_minimum(fake_screen, qtbot):
    bubble = SpeechBubble(fake_screen, duration_provider=lambda: 0.5)
    qtbot.addWidget(bubble)

    assert bubble._duration_ms("太快了", "system") == 3_000   # 下限 3s


def should_stay_inside_workarea_when_anchor_near_edge(bubble, fake_screen):
    bubble.show_text("贴边的话术", anchor=QPoint(0, 0))

    x1, y1, x2, y2 = fake_screen.geometry()
    assert bubble.x() >= x1
    assert bubble.y() >= y1
    assert bubble.x() + bubble.width() <= x2 + 1


def should_move_with_anchor_when_follow(bubble):
    bubble.show_text("跟着我", anchor=QPoint(400, 300))
    before = bubble.pos()
    bubble.follow(QPoint(120, 260))

    assert bubble.pos() != before


def should_render_unified_sticker_card_when_shown(bubble):
    """FR v1.10 C20：回答气泡与输入条/历史窗同一套圆角贴纸卡片，不再手绘尖角。"""
    bubble.show_text("渲染测试", anchor=QPoint(400, 300))

    style = bubble.styleSheet()
    assert "bubbleCard" in style
    assert "border-radius" in style
    assert "#FFFDF7" in style and "#DFC49A" in style
    assert bubble.findChild(QFrame, "bubbleCard") is not None
    assert not bubble.grab().isNull()
