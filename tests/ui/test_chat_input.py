"""ChatInput 冒烟：回车发送、空消息丢弃、ESC 关闭、会话状态标签、侧向停靠。

PRD US-3 / FR v1.9 C15 / FR v1.10 C17 / FR v1.10 C18。
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QRect, Qt

from WisePet.domain.events import PHASE_ERROR, PHASE_IDLE, PHASE_REPLYING, PHASE_THINKING
from WisePet.ui.chat_input import ChatInput


@pytest.fixture
def chat_input(qtbot, fake_screen, pet_rect):
    widget = ChatInput(fake_screen)
    qtbot.addWidget(widget)
    widget.show_near(pet_rect)
    return widget


def should_emit_submitted_and_clear_when_enter(chat_input, qtbot):
    received = []
    chat_input.submitted.connect(received.append)
    chat_input.line_edit.setText("  今天怎么样  ")

    qtbot.keyClick(chat_input.line_edit, Qt.Key_Return)

    assert received == ["今天怎么样"]
    assert chat_input.line_edit.text() == ""
    assert chat_input.isVisible()          # 回复期间输入条不消失


def should_emit_nothing_when_submit_blank(chat_input, qtbot):
    received = []
    chat_input.submitted.connect(received.append)
    chat_input.line_edit.setText("   ")

    qtbot.keyClick(chat_input.line_edit, Qt.Key_Return)

    assert received == []


def should_hide_and_dismiss_when_escape(chat_input, qtbot):
    dismissed = []
    chat_input.dismissed.connect(lambda: dismissed.append(1))

    qtbot.keyClick(chat_input.line_edit, Qt.Key_Escape)

    assert dismissed == [1]
    assert not chat_input.isVisible()


class TestSidePlacement:
    """FR v1.10 C17：左侧优先、右侧兜底，且绝不压在宠物身上。"""

    def should_sit_on_left_of_pet_when_there_is_room(self, chat_input, pet_rect):
        assert chat_input.x() + chat_input.width() <= pet_rect.left()

    def should_fall_back_to_right_when_left_has_no_room(self, chat_input, fake_screen,
                                                        pet_rect):
        left_edge = QRect(fake_screen.geometry()[0], 300, 128, 128)
        chat_input.show_near(left_edge)

        assert chat_input.x() >= left_edge.right()

    def should_stay_within_workarea_when_pet_at_edge(self, chat_input, fake_screen,
                                                     pet_rect):
        x1, y1, x2, y2 = fake_screen.geometry()
        chat_input.show_near(QRect(x1, y1, 128, 128))

        assert chat_input.x() >= x1
        assert chat_input.y() >= y1
        assert chat_input.x() + chat_input.width() <= x2 + 1
        assert chat_input.y() + chat_input.height() <= y2 + 1


class TestChatInputState:
    """历史窗不再常驻，会话阶段/大脑来源必须显示在聊天时看得见的输入条上。"""

    def should_show_phase_and_brain_when_state_changed(self, chat_input):
        chat_input.set_state(PHASE_THINKING, "ai")
        assert "思考中" in chat_input.status_text and "大模型" in chat_input.status_text

        chat_input.set_state(PHASE_REPLYING, "local")
        assert "正在回复" in chat_input.status_text and "本地小脑" in chat_input.status_text

    def should_show_error_detail_when_phase_error(self, chat_input):
        chat_input.set_state(PHASE_ERROR, "ai", detail="TIMEOUT")
        assert "出错了" in chat_input.status_text and "TIMEOUT" in chat_input.status_text

    def should_reset_to_idle_when_phase_returns(self, chat_input):
        chat_input.set_state(PHASE_THINKING, "ai")
        chat_input.set_state(PHASE_IDLE, "local")
        assert "随时可以聊" in chat_input.status_text

    def should_show_saved_record_count_and_history_hint(self, chat_input):
        chat_input.set_history_count(3)
        assert chat_input.history_count == 3
        assert "已记 3 条" in chat_input.status_text
        assert "右键" in chat_input._status.toolTip()    # 提示语挪进了 tooltip

    def should_not_show_count_when_no_records(self, chat_input):
        chat_input.set_history_count(0)
        assert "已记" not in chat_input.status_text

    def should_emit_active_then_inactive_when_shown_and_hidden(self, chat_input, pet_rect):
        states = []
        chat_input.active_changed.connect(states.append)
        chat_input.hide()
        chat_input.show_near(pet_rect)
        chat_input.hide()
        assert states == [False, True, False]

    def should_have_rounded_card_style(self, chat_input):
        assert "border-radius" in chat_input.styleSheet()
        assert "inputCard" in chat_input.styleSheet()


class TestFollowPet:
    """FR v1.10 C18：宠物移动后输入条跟着贴边，关着时不做无谓重排。"""

    def should_reposition_when_pet_moves(self, chat_input, pet_rect):
        before = chat_input.pos()
        moved = QRect(pet_rect.left() + 150, pet_rect.top() + 40, 128, 128)

        chat_input.follow_pet(moved)

        assert chat_input.pos() != before
        assert chat_input.x() + chat_input.width() <= moved.left()

    def should_ignore_follow_when_hidden(self, chat_input, pet_rect):
        chat_input.hide()
        before = chat_input.pos()

        chat_input.follow_pet(QRect(pet_rect.left() + 150, pet_rect.top(), 128, 128))

        assert chat_input.pos() == before
