"""动作协议测试：AI 回复 JSON 提取/校验 + 本地正则兜底 + 人话描述。"""
import pytest

from WisePet.app.action_protocol import (
    CreateFileAction,
    MakePptAction,
    RemindAction,
    describe_action,
    parse_action,
    parse_reminder_command,
)


class TestParseAction:
    def should_parse_fenced_json_remind(self):
        reply = '好的呀\n```json\n{"action":"remind","delay_min":5,"text":"开会"}\n```'
        assert parse_action(reply) == RemindAction(text="开会", delay_min=5.0)

    def should_parse_bare_json_create_file(self):
        reply = '{"action":"create_file","path":"D:/a.md","content":"# hi"}'
        assert parse_action(reply) == CreateFileAction(path="D:/a.md", content="# hi")

    def should_parse_ppt_with_slides(self):
        reply = ('```json\n{"action":"make_ppt","path":"C:/t.pptx","title":"汇报",'
                 '"slides":[{"title":"背景","bullets":["a","b"]},'
                 '{"title":"","bullets":[]}]}\n```')
        action = parse_action(reply)
        assert isinstance(action, MakePptAction)
        assert action.title == "汇报"
        assert len(action.slides) == 1        # 空页被清洗掉

    def should_return_none_for_plain_reply(self):
        assert parse_action("今天天气不错，适合摸鱼") is None

    def should_return_none_for_broken_json(self):
        assert parse_action('{"action":"remind","delay_min":5,"text":') is None

    def should_return_none_for_unknown_action(self):
        assert parse_action('{"action":"format_c","path":"C:"}') is None

    def should_reject_remind_without_time_or_text(self):
        assert parse_action('{"action":"remind","text":""}') is None
        assert parse_action('{"action":"remind","delay_min":0,"text":"x"}') is None

    def should_reject_absurd_delay(self):
        action = parse_action('{"action":"remind","delay_min":999999,"text":"x"}')
        assert action is None                 # 越界直接拒绝，不静默改值

    def should_ignore_braces_inside_content_strings(self):
        content = '{"action":"create_file","path":"a.json","content":"{\\"k\\": 1}"}'
        action = parse_action(content)
        assert action == CreateFileAction(path="a.json", content='{"k": 1}')


class TestParseReminderCommand:
    @pytest.mark.parametrize("text,minutes,content", [
        ("5分钟后提醒我开会", 5.0, "开会"),
        ("30秒后叫我喝水", 0.5, "喝水"),
        ("2小时后提醒我拿快递", 120.0, "拿快递"),
        ("记得3分钟后通知我交表格", 3.0, "交表格"),
        ("30s后提醒我去卫生间", 0.5, "去卫生间"),      # 真机踩过：英文单位
        ("10min后提醒我站一站", 10.0, "站一站"),
        ("半小时后叫我拿快递", 30.0, "拿快递"),
        ("五分钟后提醒我休息", 5.0, "休息"),
        ("十五分钟后提醒我开会", 15.0, "开会"),
        ("1分钟后提醒我 喝水", 1.0, "喝水"),
    ])
    def should_parse_delay_and_content(self, text, minutes, content):
        action = parse_reminder_command(text)
        if minutes is None:
            assert action is None
        else:
            assert action == RemindAction(text=content, delay_min=minutes)

    def should_return_none_without_remind_words(self):
        assert parse_reminder_command("5分钟后下班") is None

    def should_return_none_for_unsupported_forms(self):
        assert parse_reminder_command("半小时的时光很惬意") is None    # 无提醒意图词
        assert parse_reminder_command("一百分钟后提醒我") is None     # 越大数字交给 AI


class TestDescribeAction:
    def should_describe_remind_in_human_words(self):
        assert describe_action(RemindAction(text="开会", delay_min=5.0)) == \
            "5分钟后提醒你「开会」"

    def should_describe_ppt_with_page_count(self):
        action = MakePptAction(path="a.pptx", title="汇报",
                               slides=({"title": "背景", "bullets": ("a",)},))
        assert "1 页" in describe_action(action) and "汇报" in describe_action(action)
