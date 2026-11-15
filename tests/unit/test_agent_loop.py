"""轴② agent 工具循环：function calling 多步 / 降级 / 步数上限 / 待确认动作。"""
from __future__ import annotations

from dataclasses import replace

import pytest
from fakes import FakeClock, FakeGateway, make_collecting_bus

from WisePet.app.agent_loop import AgentRunner, MAX_STEPS
from WisePet.app.agent_tools import TOOL_SCHEMAS, AgentToolbox
from WisePet.app.knowledge_base import KnowledgeBase
from WisePet.app.task_service import TaskService
from WisePet.app.chat_service import ChatService
from WisePet.domain.chat_session import ChatSession
from WisePet.domain.config_model import AppConfig
from WisePet.domain.errors import AIGatewayError
from WisePet.domain.events import ChatReplyChunkEvent
from WisePet.domain.ports import AIConfig
from WisePet.infra.knowledge_store import KnowledgeStore
from WisePet.infra.task_store import TaskStore

BASE_TS = 1_700_000_000.0


def _cfg(**assistant_changes):
    base = AppConfig()
    return replace(base, assistant=replace(base.assistant, **assistant_changes))


def _call(name: str, **args):
    return {"id": f"call-{name}", "name": name, "arguments": args}


class _FakeMemory:
    def __init__(self):
        self.facts_dict = {"user_name": "小明", "activity": "", "project": ""}

    def load(self):
        return dict(self.facts_dict)

    def load_history(self):
        return []

    def facts(self):
        return "用户名叫小明"


def _toolbox(tmp_path):
    folder = tmp_path / "notes"
    folder.mkdir()
    (folder / "plan.md").write_text("周报计划：周一交周报", encoding="utf-8")
    cfg = _cfg(knowledge_dir=str(folder))
    return AgentToolbox(
        knowledge=KnowledgeBase(KnowledgeStore(tmp_path)),
        chat_history=None, memory_service=_FakeMemory(),
        tasks=TaskService(FakeClock(), None, lambda: cfg,
                          TaskStore(tmp_path)),
        pet=None, config_provider=lambda: cfg,
        remind_callback=lambda action: None), folder, cfg


def _ai_config():
    return AIConfig(base_url="https://x/v1", api_key="k", model="m")


def _runner(gateway, toolbox, cfg=None):
    cfg = cfg or _ai_config()
    return AgentRunner(gateway, lambda: cfg, lambda: "你是桌面宠物。", toolbox)


def _chat(gateway, tmp_path, runner=None):
    bus, collected = make_collecting_bus()
    service = ChatService(
        gateway=gateway, session=ChatSession(), bus=bus,
        config_provider=lambda: _ai_config(),
        system_prompt_provider=lambda: "你是桌面宠物。",
        local_responder=None, tool_runner=runner)
    return service, collected


# ---------- 工具箱 ----------

class TestToolbox:
    def should_read_file_inside_knowledge_folder(self, tmp_path):
        toolbox, _folder, _cfg_tool = _toolbox(tmp_path)
        text, action = toolbox.execute("read_file", {"path": "plan.md"})
        assert action is None and "周报计划" in text

    def should_reject_read_outside_knowledge_folder(self, tmp_path):
        toolbox, _folder, _cfg_tool = _toolbox(tmp_path)
        outside = tmp_path / "secret.txt"
        outside.write_text("机密", encoding="utf-8")
        text, action = toolbox.execute("read_file", {"path": str(outside)})
        assert action is None and "资料文件夹" in text

    def should_stop_directory_traversal(self, tmp_path):
        toolbox, _folder, _cfg_tool = _toolbox(tmp_path)
        text, _action = toolbox.execute("read_file", {"path": "../secret.txt"})
        assert "资料文件夹" in text

    def should_list_folder(self, tmp_path):
        toolbox, _folder, _cfg_tool = _toolbox(tmp_path)
        text, _action = toolbox.execute("list_folder", {})
        assert "plan.md" in text

    def should_search_knowledge(self, tmp_path):
        toolbox, folder, _cfg_tool = _toolbox(tmp_path)
        toolbox._knowledge.build(str(folder), now=123.0)
        text, _action = toolbox.execute("search_knowledge", {"query": "周报"})
        assert "周报计划" in text

    def should_return_action_for_create_file(self, tmp_path):
        toolbox, _folder, _cfg_tool = _toolbox(tmp_path)
        text, action = toolbox.execute(
            "create_file", {"path": "D:/notes/todo.md", "content": "写点东西"})
        assert action is not None
        assert action.path == "D:/notes/todo.md" and action.content == "写点东西"

    def should_route_remind_via_callback(self, tmp_path):
        scheduled = []
        toolbox, _folder, _cfg_tool = _toolbox(tmp_path)
        toolbox._remind_callback = lambda action: scheduled.append(action)
        text, action = toolbox.execute("remind", {"delay_min": 5, "text": "开会"})
        assert action is None and scheduled and scheduled[0].text == "开会"

    def should_expose_valid_tool_schemas(self):
        names = [t["function"]["name"] for t in TOOL_SCHEMAS]
        assert {"read_file", "list_folder", "search_knowledge", "search_history",
                "recall_memories", "get_status", "remind",
                "create_file", "make_ppt"} <= set(names)
        for schema in TOOL_SCHEMAS:
            assert schema["type"] == "function"
            assert "parameters" in schema["function"]


# ---------- Agent 循环 ----------

class TestAgentLoop:
    def should_answer_directly_without_tools(self, tmp_path):
        toolbox, _folder, _cfg_tool = _toolbox(tmp_path)
        gateway = FakeGateway(tools_results=[("你好呀，我在这儿～", [])])
        outcome = _runner(gateway, toolbox).run("在吗", [])
        assert outcome.final_text == "你好呀，我在这儿～"
        assert outcome.pending_action is None and outcome.steps == 1
        assert outcome.tools_used == []

    def should_run_multi_step_tool_loop_then_answer(self, tmp_path):
        toolbox, _folder, _cfg_tool = _toolbox(tmp_path)
        gateway = FakeGateway(tools_results=[
            ("", [_call("list_folder")]),
            ("", [_call("read_file", path="plan.md")]),
            ("我看了你的资料：周报计划是周一交周报。", []),
        ])
        outcome = _runner(gateway, toolbox).run("我笔记里写了什么计划？", [])
        assert "周报计划" in outcome.final_text
        assert outcome.tools_used == ["list_folder", "read_file"]
        assert outcome.steps == 3

    def should_yield_pending_action_for_create_file(self, tmp_path):
        toolbox, _folder, _cfg_tool = _toolbox(tmp_path)
        gateway = FakeGateway(tools_results=[
            ("", [_call("create_file", path="D:/out.md", content="内容")])])
        outcome = _runner(gateway, toolbox).run("帮我写个文件", [])
        assert outcome.pending_action is not None
        assert outcome.pending_action.path == "D:/out.md"
        assert "等待用户确认" in outcome.final_text

    def should_force_summary_after_max_steps(self, tmp_path):
        toolbox, _folder, _cfg_tool = _toolbox(tmp_path)
        gateway = FakeGateway(tools_results=[("", [_call("list_folder")])] * MAX_STEPS
                              + [("该总结了", [])])
        outcome = _runner(gateway, toolbox).run("随便看看", [])
        assert outcome.steps == MAX_STEPS
        assert "该总结了" in outcome.final_text

    def should_fall_back_to_plain_stream_when_gateway_rejects_tools(self, tmp_path):
        toolbox, _folder, _cfg_tool = _toolbox(tmp_path)
        gateway = FakeGateway(chunks=["你好", "呀"],
                              tools_error=AIGatewayError(
                                  AIGatewayError.PROTOCOL, "tools not supported"))
        outcome = _runner(gateway, toolbox).run("在吗", [])
        assert outcome.final_text == "你好呀" and outcome.degraded is True
        assert gateway.calls                       # 走了 stream_chat 降级

    def should_let_auth_errors_propagate(self, tmp_path):
        toolbox, _folder, _cfg_tool = _toolbox(tmp_path)
        gateway = FakeGateway(tools_error=AIGatewayError(
            AIGatewayError.AUTH, "api_key 未配置"))
        with pytest.raises(AIGatewayError):
            _runner(gateway, toolbox).run("在吗", [])

    def should_feed_tool_result_back_as_tool_message(self, tmp_path):
        toolbox, _folder, _cfg_tool = _toolbox(tmp_path)
        gateway = FakeGateway(tools_results=[
            ("", [_call("read_file", path="plan.md")]),
            ("念完了", [])])
        _runner(gateway, toolbox).run("念念我的笔记", [])
        second_messages = gateway.tool_calls[1][0]
        tool_msgs = [m for m in second_messages if m.get("role") == "tool"]
        assert tool_msgs and "周报计划" in tool_msgs[0]["content"]

    def should_remember_disabled_gateways_per_key(self, tmp_path):
        toolbox, _folder, _cfg_tool = _toolbox(tmp_path)
        gateway = FakeGateway(tools_error=AIGatewayError(
            AIGatewayError.PROTOCOL, "no tools"))
        runner = _runner(gateway, toolbox)
        runner.run("一", [])
        assert runner.run("二", []).degraded is True
        assert len(gateway.tool_calls) == 1        # 只试过一次 tools

    def should_record_tool_calls_in_log_messages(self, tmp_path):
        toolbox, _folder, _cfg_tool = _toolbox(tmp_path)
        gateway = FakeGateway(tools_results=[
            ("", [_call("search_knowledge", query="周报")]),
            ("找到了", [])])
        _runner(gateway, toolbox).run("查资料", [])
        first_messages = gateway.tool_calls[0][0]
        assistant = [m for m in first_messages if m.get("role") == "assistant"]
        assert assistant and assistant[-1]["tool_calls"][0]["function"]["name"] \
            == "search_knowledge"


# ---------- ChatService 集成 ----------

def test_chat_service_uses_agent_and_emits_pending_action(tmp_path):
    toolbox, _folder, _cfg_tool = _toolbox(tmp_path)
    gateway = FakeGateway(tools_results=[
        ("", [_call("create_file", path="D:/notes/todo.md", content="待办")])])
    service, collected = _chat(gateway, tmp_path,
                               runner=lambda text, messages, on_content=None: _runner(
                                   gateway, toolbox).run(text, messages,
                                                         content_callback=on_content))
    service.send("帮我写个文件")
    service.process_next()

    done = [e for e in collected if isinstance(e, ChatReplyChunkEvent) and e.done]
    assert done and done[0].pending_action is not None
    assert done[0].pending_action.path == "D:/notes/todo.md"
    assert "确认" in done[0].reply or "准备" in done[0].reply


def test_chat_service_plain_path_still_works_when_runner_disabled(tmp_path):
    toolbox, _folder, _cfg_tool = _toolbox(tmp_path)
    gateway = FakeGateway(chunks=["你好", "呀"])
    service, collected = _chat(gateway, tmp_path,
                               runner=lambda text, m, on_content=None: None)
    service.send("在吗")
    service.process_next()

    done = [e for e in collected if isinstance(e, ChatReplyChunkEvent) and e.done]
    assert done and done[0].reply == "你好呀" and done[0].pending_action is None


def test_chat_service_agent_failure_reports_error(tmp_path):
    toolbox, _folder, _cfg_tool = _toolbox(tmp_path)
    gateway = FakeGateway(tools_error=AIGatewayError(AIGatewayError.TIMEOUT, "t"))
    service, collected = _chat(gateway, tmp_path,
                               runner=lambda text, m, on_content=None: _runner(
                                   gateway, toolbox).run(text, m,
                                                         content_callback=on_content))
    service.send("在吗")
    service.process_next()

    errors = [e for e in collected if type(e).__name__ == "ChatErrorEvent"]
    assert errors and errors[0].kind == "timeout"
