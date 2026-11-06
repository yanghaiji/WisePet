"""ChatSession 聚合测试：消息校验、历史截断、prompt 组装（不变量 C1/C2/C3）。"""
import pytest

from WisePet.domain.chat_session import ChatSession, Turn


@pytest.fixture()
def session():
    return ChatSession(session_id="s1", max_turns=2)


class TestChatSessionAppend:
    def should_append_valid_turn_when_content_given(self, session):
        session.add_turn("user", "你好")
        assert session.turns == [Turn(role="user", content="你好")]

    def should_reject_empty_message_when_blank(self, session):
        """不变量 C2：空/纯空白消息拒绝。"""
        with pytest.raises(ValueError):
            session.add_turn("user", "   ")

    def should_reject_unknown_role_when_not_user_or_assistant(self, session):
        with pytest.raises(ValueError):
            session.add_turn("system", "hack")

    def should_truncate_earliest_when_over_max_rounds(self, session):
        """不变量 C1：max_turns=2 → 最多保留 4 条；第 5 条进入时裁掉最早。"""
        for i in range(5):
            session.add_turn("user", f"msg{i}")
        assert len(session.turns) == 4
        assert session.turns[0].content == "msg1"
        assert session.turns[-1].content == "msg4"


class TestChatSessionMessages:
    def should_prepend_system_prompt_when_building_messages(self, session):
        session.add_turn("user", "hi")
        session.add_turn("assistant", "hello")
        messages = session.build_messages(system_prompt="你是小墨")
        assert messages[0] == {"role": "system", "content": "你是小墨"}
        assert messages[1] == {"role": "user", "content": "hi"}
        assert messages[2] == {"role": "assistant", "content": "hello"}

    def should_expose_recent_reply_marker_when_assistant_last(self, session):
        session.add_turn("user", "hi")
        session.add_turn("assistant", "hello")
        assert session.last_role == "assistant"


class TestChatSessionTruncationBoundary:
    def should_not_truncate_when_exactly_at_limit(self):
        session = ChatSession(max_turns=2)
        for i in range(4):
            session.add_turn("user", f"m{i}")
        assert len(session.turns) == 4
        assert session.turns[0].content == "m0"
