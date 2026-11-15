"""ChatWindow 冒烟：可继续聊天的历史窗（气泡渲染、按天回看、状态、跟随、贴纸风）。

FR v1.10 C18/C19：历史窗既能回看也能直接接着聊，且跟着宠物移动。
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QRect, Qt

from WisePet.domain.chat_record import ChatRecord, day_key
from WisePet.domain.events import PHASE_ERROR, PHASE_REPLYING, PHASE_THINKING
from WisePet.ui.chat_window import ChatWindow, _bubble_html


def _record(role, content, source="", ts=1_700_000_000.0):
    return ChatRecord(role=role, content=content, source=source, ts=ts,
                      session_id=day_key(ts))


class TestMarkdownRendering:
    """v1.33 打磨：助手消息完整渲染 markdown，用户消息保持纯文本。"""

    def should_render_markdown_for_assistant_record(self):
        html = _bubble_html(_record("assistant", "# 标题\n**加粗**\n\n- 项", "ai"))
        assert "<h1" in html
        assert "<strong>加粗</strong>" in html
        assert "<li>项</li>" in html
        assert "**加粗**" not in html          # 星号标记不能原样露出

    def should_escape_raw_html_for_assistant_record(self):
        html = _bubble_html(_record("assistant", "<script>x</script>", "ai"))
        assert "&lt;script&gt;" in html and "<script>" not in html

    def should_keep_user_content_plain(self):
        html = _bubble_html(_record("user", "**不解释**", "user"))
        assert "**不解释**" in html          # 用户原文原样（转义后）
        assert "<strong>" not in html

    def should_render_table_and_code_block(self):
        html = _bubble_html(_record("assistant", "```py\nprint(1)\n```", "ai"))
        assert "<pre" in html and "print(1)" in html


@pytest.fixture
def chat_window(qtbot, fake_screen):
    widget = ChatWindow(fake_screen,
                        days_provider=lambda: ["2026-11-04", "2026-11-05"],
                        records_provider=lambda _day: [_record("user", "昨天的问候", "user")])
    qtbot.addWidget(widget)
    return widget


class TestFileDrop:
    """v1.33 打磨：文件拖进输入框/窗口 → 走附件链路（与 📎 按钮同一汇合点）。"""

    def should_turn_dropped_paths_into_attachments(self, chat_window, tmp_path):
        target = tmp_path / "拖进来的.txt"
        target.write_text("内容", encoding="utf-8")
        chat_window._add_dropped_files([str(target)])
        taken = chat_window.take_attachments()
        assert len(taken) == 1 and taken[0].name == "拖进来的.txt"

    def should_report_error_and_keep_valid_ones(self, chat_window, tmp_path):
        good = tmp_path / "good.txt"
        good.write_text("内容", encoding="utf-8")
        bad = tmp_path / "bad.exe"        # 不在支持类型里
        bad.write_text("x", encoding="utf-8")
        chat_window._add_dropped_files([str(good), str(bad)])
        taken = chat_window.take_attachments()
        assert [a.name for a in taken] == ["good.txt"]
        assert "一次最多" not in chat_window._files.text()

    def should_respect_attachment_limit_when_dropped(self, chat_window, tmp_path,
                                                     monkeypatch):
        from WisePet.domain.attachment import MAX_ATTACHMENTS
        monkeypatch.setattr("WisePet.ui.chat_text_edit.MAX_ATTACHMENTS", 1)
        one = tmp_path / "one.txt"
        one.write_text("1", encoding="utf-8")
        two = tmp_path / "two.txt"
        two.write_text("2", encoding="utf-8")
        chat_window._add_dropped_files([str(one), str(two)])
        # 先断言提示（take_attachments 会清空文件标签），再取走附件
        assert "一次最多 1 个附件" in chat_window._files.text()
        assert "one.txt" in chat_window._files.text()
        taken = chat_window.take_attachments()
        assert [a.name for a in taken] == ["one.txt"]


class TestWindowControls:
    """v1.33 打磨：— ▢ × 控制按钮；最大化铺满工作区、还原到之前几何。"""

    def should_have_window_control_buttons(self, chat_window):
        assert chat_window._min_btn.text() == "—"
        assert chat_window._max_btn.text() == "▢"
        assert chat_window._close_btn.text() == "×"

    def should_maximize_fill_workarea_and_restore(self, chat_window, fake_screen):
        x1, y1, x2, y2 = fake_screen.geometry()
        before = QRect(chat_window.pos(), chat_window.size())
        chat_window._max_btn.click()
        assert chat_window._maximized
        assert chat_window.x() == x1 + 8 and chat_window.y() == y1 + 8
        assert chat_window.width() == (x2 - x1 + 1) - 16
        chat_window._max_btn.click()               # 还原
        assert not chat_window._maximized
        assert QRect(chat_window.pos(), chat_window.size()) == before

    def should_hide_when_minimize_clicked(self, chat_window):
        chat_window.show()
        chat_window._min_btn.click()
        assert not chat_window.isVisible()


class TestContinueChatting:
    """FR v1.10 C19：历史区只读，底部输入行可直接接着聊。"""

    def should_expose_submit_signal_and_line_edit(self, chat_window):
        assert hasattr(chat_window, "submitted")
        assert chat_window.line_edit is not None
        assert chat_window._history.isReadOnly()       # 历史区本身仍只读

    def should_emit_submitted_and_clear_when_enter(self, chat_window):
        received = []
        chat_window.submitted.connect(received.append)
        chat_window.line_edit.setText("  接着聊  ")

        chat_window.line_edit.send_requested.emit()

        assert received == ["接着聊"]
        assert chat_window.line_edit.text() == ""

    def should_emit_submitted_when_send_button_clicked(self, chat_window):
        received = []
        chat_window.submitted.connect(received.append)
        chat_window.line_edit.setText("再问一句")

        chat_window.send_button.click()

        assert received == ["再问一句"]

    def should_drop_blank_when_submitting(self, chat_window):
        received = []
        chat_window.submitted.connect(received.append)
        chat_window.line_edit.setText("   ")

        chat_window.line_edit.send_requested.emit()

        assert received == []

    def should_hide_on_escape_from_input_line(self, chat_window, qtbot):
        chat_window.show_near(QRect(400, 300, 128, 128))

        qtbot.keyClick(chat_window.line_edit, Qt.Key_Escape)

        assert not chat_window.isVisible()

    def should_be_frameless_translucent_card(self, chat_window):
        assert chat_window.windowFlags() & Qt.FramelessWindowHint
        assert chat_window.testAttribute(Qt.WA_TranslucentBackground)
        assert chat_window.findChild(type(chat_window._status), "status") is not None
        assert "border-radius" in chat_window.styleSheet()
        assert "historyCard" in chat_window.styleSheet()

    def should_have_close_button_and_escape_shortcut(self, chat_window):
        assert chat_window.close_button.text() == "×"
        chat_window.show_near(QRect(400, 300, 128, 128))
        chat_window.close_button.click()
        assert not chat_window.isVisible()


class TestFollowPet:
    """FR v1.10 C18：宠物挪窝后重新贴边；用户手动拖过就不再被拽回。"""

    def should_reposition_when_pet_moves(self, chat_window, pet_rect):
        chat_window.show_near(pet_rect)
        before = chat_window.pos()
        moved = QRect(pet_rect.left() + 130, pet_rect.top() + 60, 128, 128)

        chat_window.follow_pet(moved)

        assert chat_window.pos() != before
        assert chat_window.x() + chat_window.width() <= moved.left()

    def should_ignore_follow_when_hidden(self, chat_window, pet_rect):
        before = chat_window.pos()
        chat_window.follow_pet(QRect(pet_rect.left() + 130, pet_rect.top(), 128, 128))
        assert chat_window.pos() == before

    def should_stay_put_after_user_drags_window(self, chat_window, pet_rect):
        chat_window.show_near(pet_rect)
        chat_window._begin_drag(QPoint(10, 10))
        chat_window._move_by(QPoint(60, 20))
        pinned = chat_window.pos()

        chat_window.follow_pet(QRect(pet_rect.left() + 130, pet_rect.top(), 128, 128))

        assert chat_window.pos() == pinned


class TestHistoryRendering:
    def should_show_history_and_source_when_loaded(self, chat_window):
        chat_window.load_history([
            _record("user", "你好<呀>", "user"),
            _record("assistant", "在呢", "local"),
        ])

        html = chat_window._history.toHtml()
        plain = chat_window._history.toPlainText()
        assert "你好&lt;呀&gt;" in html           # 转义进 HTML，不会被当成标签执行
        assert "你好<呀>" in plain
        assert "本地小脑" in plain
        assert "2 条" in chat_window.status_text

    def should_render_user_and_assistant_bubbles_with_distinct_colors(self, chat_window):
        chat_window.load_history([
            _record("user", "今天怎么样", "user"),
            _record("assistant", "挺好", "local"),
        ])

        html = chat_window._history.toHtml().lower()
        assert "#f8d79b" in html                  # 用户暖橘（Qt 规范化为小写）
        assert "#ffffff" in html                  # 助手奶油白
        assert 'align="right"' in html
        # 左对齐是 Qt 富文本默认值，规范化后会省略 align，故对生成源串断言
        assert 'align="right"' in _bubble_html(_record("user", "a", "user"))
        assert 'align="left"' in _bubble_html(_record("assistant", "b", "local"))

    def should_mark_fallback_reply_with_own_color(self, chat_window):
        chat_window.load_history([_record("assistant", "连不上", "fallback")])
        assert "#f7e2e2" in chat_window._history.toHtml().lower()

    def should_escape_html_in_bubble_source(self):
        assert "&lt;script&gt;" in _bubble_html(
            _record("user", "<script>alert(1)</script>", "user"))
        assert "<script>" not in _bubble_html(
            _record("user", "<script>alert(1)</script>", "user"))

    def should_show_empty_hint_when_no_records(self, chat_window):
        chat_window.load_history([])
        assert "还没有聊天记录" in chat_window._history.toPlainText()

    def should_clear_empty_hint_when_first_message_arrives(self, chat_window):
        chat_window.load_history([])
        chat_window.append_record(_record("user", "你好", "user"))

        text = chat_window._history.toPlainText()
        assert "你好" in text and "还没有聊天记录" not in text
        assert "1 条" in chat_window.status_text

    def should_not_append_new_message_when_viewing_past_day(self, chat_window):
        chat_window.reload()
        chat_window.day_combo.setCurrentText("2026-11-04")
        before = chat_window._history.toPlainText()

        chat_window.receive_record(_record("user", "今天的新消息", "user"))

        assert "今天的新消息" not in chat_window._history.toPlainText()
        assert chat_window._history.toPlainText() == before
        assert "1 条" in chat_window.status_text

    def should_append_new_message_when_viewing_today(self, chat_window):
        chat_window.reload()
        assert chat_window.session_day == day_key()

        chat_window.receive_record(_record("user", "今天的新消息", "user"))

        assert "今天的新消息" in chat_window._history.toPlainText()


class TestStateAndDays:
    def should_show_phase_and_brain_when_state_changed(self, chat_window):
        chat_window.set_state(PHASE_THINKING, "ai")
        assert "思考中" in chat_window.status_text and "大模型" in chat_window.status_text

        chat_window.set_state(PHASE_REPLYING, "local")
        assert "正在回复" in chat_window.status_text and "本地小脑" in chat_window.status_text

    def should_show_error_detail_when_phase_error(self, chat_window):
        chat_window.set_state(PHASE_ERROR, "ai", detail="TIMEOUT")
        assert "出错了" in chat_window.status_text and "TIMEOUT" in chat_window.status_text

    def should_load_selected_day_history_when_combo_changed(self, chat_window):
        chat_window.reload()
        assert "昨天的问候" in chat_window._history.toPlainText()
        assert chat_window.session_day == day_key()

    def should_reload_history_for_another_day(self, chat_window):
        chat_window.reload()
        chat_window.day_combo.setCurrentText("2026-11-04")
        assert chat_window.session_day == "2026-11-04"


class TestVisibility:
    def should_emit_active_true_then_false_when_opened_and_hidden(self, chat_window, pet_rect):
        states = []
        chat_window.active_changed.connect(states.append)

        chat_window.show_near(pet_rect)
        assert chat_window.isVisible() and states == [True]

        chat_window.hide()
        assert not chat_window.isVisible() and states == [True, False]

    def should_toggle_visibility_when_triggered_twice(self, chat_window, pet_rect):
        chat_window.toggle(pet_rect)
        assert chat_window.isVisible()
        chat_window.toggle(pet_rect)
        assert not chat_window.isVisible()

    def should_hide_on_escape_and_emit_inactive(self, chat_window, pet_rect, qtbot):
        states = []
        chat_window.active_changed.connect(states.append)
        chat_window.show_near(pet_rect)

        chat_window.setFocus()
        qtbot.keyClick(chat_window, Qt.Key_Escape)

        assert not chat_window.isVisible() and states == [True, False]


class TestSidePlacement:
    """FR v1.10 C17：历史窗也贴宠物左侧，左侧不够放右侧，绝不压住宠物。"""

    def should_sit_on_left_when_there_is_room(self, chat_window, pet_rect):
        chat_window.show_near(pet_rect)
        assert chat_window.x() + chat_window.width() <= pet_rect.left()

    def should_fall_back_to_right_when_left_has_no_room(self, chat_window, fake_screen):
        x1 = fake_screen.geometry()[0]
        edge_pet = QRect(x1, 300, 128, 128)
        chat_window.show_near(edge_pet)
        assert chat_window.x() >= edge_pet.right()

    def should_stay_within_workarea_when_pet_at_edge(self, chat_window, fake_screen):
        x1, y1, x2, y2 = fake_screen.geometry()
        chat_window.show_near(QRect(x1, y1, 128, 128))
        assert chat_window.x() >= x1
        assert chat_window.y() >= y1
        assert chat_window.x() + chat_window.width() <= x2 + 1
        assert chat_window.y() + chat_window.height() <= y2 + 1
