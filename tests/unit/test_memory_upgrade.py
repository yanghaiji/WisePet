"""记忆体系升级 v1.31：会话摘要 / 实体记忆泛化 / 语义检索 / 本地身份。"""
from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from fakes import FakeClock, FakeGateway, make_collecting_bus

from WisePet.app.agent_tools import AgentToolbox
from WisePet.app.chat_service import ChatService
from WisePet.app.knowledge_base import KnowledgeBase
from WisePet.app.summary_memory import SummaryMemory, SummaryStore
from WisePet.app.long_term_memory import LongTermMemory
from WisePet.domain.chat_session import ChatSession
from WisePet.domain.config_model import AppConfig
from WisePet.domain.errors import AIGatewayError
from WisePet.domain.ports import AIConfig
from WisePet.infra.knowledge_store import KnowledgeStore
from WisePet.infra.product_store import MemoryStore
from WisePet.infra.profile_store import ProfileStore, guard_port_for, safe_name


def _cfg(**kw):
    return AIConfig(base_url="https://x/v1", api_key="k", model="m",
                    embedding_model=kw.get("embedding_model", ""))


def _enabled(flag=True):
    return lambda: flag


# ---------- 1. 会话摘要 ----------

class _CompleteGateway:
    """只实现 complete 的网关替身（摘要生成走非流式）。"""

    def __init__(self, replies=(), error=None):
        self.replies = list(replies)
        self.error = error
        self.calls = []

    def complete(self, messages, cfg):
        self.calls.append((messages, cfg))
        if self.error:
            raise self.error
        if not self.replies:
            raise AssertionError("CompleteGateway.replies 已耗尽")
        return self.replies.pop(0)

    def stream_chat(self, messages, cfg):   # pragma: no cover - 摘要不走流式
        yield ""


class TestSummaryMemory:
    def _memory(self, tmp_path, gateway=None, enabled=True):
        gateway = gateway or _CompleteGateway(replies=["用户在忙周报，讨厌香菜。"])
        return SummaryMemory(
            SummaryStore(tmp_path), gateway, lambda: _cfg(),
            _enabled(enabled), FakeClock()), gateway

    def should_return_none_when_empty_or_disabled(self, tmp_path):
        mem, _gw = self._memory(tmp_path, enabled=False)
        assert mem.prefix_line() is None
        mem2, _gw2 = self._memory(tmp_path)
        assert mem2.prefix_line() is None

    def should_buffer_then_summarize_async(self, tmp_path):
        mem, gateway = self._memory(tmp_path)
        for _ in range(8):                       # DEFAULT_BUFFER_LIMIT = 8
            mem.observe("user", "我要写周报")
            mem.observe("assistant", "好呀")
        import time as _t
        deadline = _t.time() + 2.0
        while gateway.calls and _t.time() < deadline:
            _t.sleep(0.05)
        assert gateway.calls                     # 后台线程已生成
        assert mem.prefix_line() and "周报" in mem.prefix_line()
        assert SummaryStore(tmp_path).load()["text"]

    def should_silently_survive_ai_failure(self, tmp_path):
        gateway = _CompleteGateway(error=AIGatewayError(AIGatewayError.TIMEOUT, "t"))
        mem, _gw = self._memory(tmp_path, gateway=gateway)
        for _ in range(8):
            mem.observe("user", "随便聊")
        import time as _t
        _t.sleep(0.3)                            # 后台线程吞掉异常
        assert mem.prefix_line() is None         # 没有摘要也不炸

    def should_reconcile_truncated_history_after_restart(self, tmp_path):
        mem, _gw = self._memory(tmp_path)
        records = [SimpleNamespace(role="user", content="早上说想吃面"),
                   SimpleNamespace(role="assistant", content="好呀"),
                   SimpleNamespace(role="system", content="ignored")]
        turns = [("user", "后来改吃饺子"), ("assistant", "也好")]
        mem.reconcile(records, turns)
        assert mem.state()["buffered"] == 2      # system 不进缓冲

    def should_clear_store_and_buffer(self, tmp_path):
        mem, _gw = self._memory(tmp_path)
        mem._summary = "旧的"
        SummaryStore(tmp_path).save("旧的")
        mem.clear()
        assert mem.prefix_line() is None
        assert mem.state()["buffered"] == 0


# ---------- 2. 实体记忆泛化 ----------

class TestGeneralizedMemory:
    def _memory(self, tmp_path, enabled=True):
        return LongTermMemory(MemoryStore(tmp_path), _enabled(enabled),
                              clock=FakeClock())

    def should_remember_free_form_facts(self, tmp_path):
        mem = self._memory(tmp_path)
        assert mem.remember("养了只猫", "橘猫，叫胖虎") is True
        assert mem.remember("讨厌", "香菜") is True
        assert len(mem.entries()) == 2
        assert "养了只猫：橘猫" in mem.prompt_line()

    def should_overwrite_same_key_and_cap_50(self, tmp_path):
        mem = self._memory(tmp_path)
        mem.remember("养了只猫", "橘猫")
        mem.remember("养了只猫", "英短")          # 同 key 覆盖
        assert len(mem.entries()) == 1
        assert mem.entries()[0]["value"] == "英短"
        for i in range(60):
            mem.remember(f"事实{i}", "v")
        assert len(mem.entries()) == 50

    def should_reject_blank_or_disabled(self, tmp_path):
        assert self._memory(tmp_path, enabled=False).remember("k", "v") is False
        assert self._memory(tmp_path).remember("", "v") is False

    def should_delete_entry_and_reload(self, tmp_path):
        mem = self._memory(tmp_path)
        mem.remember("养了只猫", "橘猫")
        assert mem.delete_entry(0) is True
        assert mem.entries() == [] and mem.prompt_line() == ""

    def should_persist_facts_across_restart(self, tmp_path):
        mem = self._memory(tmp_path)
        mem.remember("养了只猫", "橘猫")
        again = self._memory(tmp_path)
        assert again.entries()[0]["value"] == "橘猫"

    def should_expose_remember_tool_via_agent(self, tmp_path):
        mem = self._memory(tmp_path)
        toolbox = AgentToolbox(memory_service=mem, config_provider=lambda: AppConfig())
        text, action = toolbox.execute("remember", {"key": "养了只猫",
                                                    "value": "橘猫叫胖虎"})
        assert action is None and "记住" in text
        assert mem.entries()[0]["value"] == "橘猫叫胖虎"

    def should_show_facts_in_memory_timeline(self, tmp_path):
        mem = self._memory(tmp_path)
        mem.remember("养了只猫", "橘猫")
        from WisePet.app.memory_timeline import MemoryTimeline
        timeline = MemoryTimeline(memory_store=MemoryStore(tmp_path),
                                  memory_service=mem)
        ids = [e.id for e in timeline.entries()]
        fact_id = next(i for i in ids if i.startswith("memory_fact:"))
        assert timeline.forget([fact_id]) == 1
        assert mem.entries() == []           # 真删 + reload 内存态


# ---------- 3. 语义检索 ----------

class _FakeEmbedder:
    """词袋 embedding 替身：出现「事故」的文本向量指向同一方向。"""

    def __call__(self, texts):
        out = []
        for text in texts:
            v = [0.0, 0.0, 1.0] if "事故" in text else [1.0, 0.0, 0.0]
            out.append(v)
        return out


class TestSemanticSearch:
    def _kb(self, tmp_path, embedder=None):
        folder = tmp_path / "notes"
        folder.mkdir()
        (folder / "deploy.md").write_text("上周上线出了一次事故，回滚了", encoding="utf-8")
        (folder / "food.md").write_text("今天吃了火锅，很辣", encoding="utf-8")
        return KnowledgeBase(KnowledgeStore(tmp_path),
                             embedding_fn=embedder), folder

    def should_embed_chunks_on_build_and_mix_scores(self, tmp_path):
        kb, folder = self._kb(tmp_path, _FakeEmbedder())
        kb.build(str(folder), now=123.0)
        status = kb.status()
        assert status["vectors"] == 2 and status["semantic"] is True
        hits = kb.search("出了什么事故", top_k=2)   # 与 deploy.md 无关键词重叠
        assert hits and "deploy.md" in hits[0]["path"]

    def should_fall_back_to_bm25_without_embedder(self, tmp_path):
        kb, folder = self._kb(tmp_path)
        kb.build(str(folder), now=123.0)
        assert kb.status()["vectors"] == 0
        hits = kb.search("事故", top_k=2)           # 纯关键词仍可用
        assert hits and "deploy.md" in hits[0]["path"]

    def should_degrade_when_embedder_fails(self, tmp_path):
        def boom(_texts):
            raise AIGatewayError(AIGatewayError.NETWORK, "down")
        kb, folder = self._kb(tmp_path, boom)
        kb.build(str(folder), now=123.0)
        assert kb.status()["vectors"] == 0          # 构建时失败 → 纯关键词
        assert kb.search("事故")                     # 检索不炸


# ---------- 4. 本地身份 ----------

class TestProfiles:
    def should_validate_names(self):
        assert safe_name("小明") == "小明"
        assert safe_name("dev_2") == "dev_2"
        with pytest.raises(ValueError):
            safe_name("../evil")
        with pytest.raises(ValueError):
            safe_name("a/b")

    def should_derive_isolated_data_dirs(self, tmp_path):
        store = ProfileStore(tmp_path)
        assert store.data_dir_for("") == tmp_path          # 默认身份零迁移
        path = store.data_dir_for("小明")
        assert path == tmp_path / "profiles" / "小明" and path.is_dir()

    def should_persist_active_profile(self, tmp_path):
        store = ProfileStore(tmp_path)
        assert store.active() == ""
        store.set_active("小明")
        assert store.active() == "小明"
        assert "小明" in ProfileStore(tmp_path).list()

    def should_derive_guard_port_per_profile(self):
        assert guard_port_for("") == 45789
        assert guard_port_for("小明") != guard_port_for("小红")
        assert 45790 <= guard_port_for("小明") <= 45789 + 999


# ---------- 5. embeddings 客户端（httpx MockTransport） ----------

def test_embeddings_client_parses_openai_shape():
    from WisePet.infra.openai_client import OpenAICompatClient
    body = {"data": [{"index": 1, "embedding": [0.5, 0.0]},
                     {"index": 0, "embedding": [0.0, 1.0]}]}
    handler = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    original = httpx.Client
    try:
        httpx.Client = lambda **kw: original(transport=handler, **kw)
        vectors = OpenAICompatClient().embeddings(["a", "b"], _cfg(embedding_model="e3"))
    finally:
        httpx.Client = original
    assert vectors == [[0.0, 1.0], [0.5, 0.0]]     # index 排序复原


def test_embeddings_client_classifies_errors():
    from WisePet.infra.openai_client import OpenAICompatClient
    handler = httpx.MockTransport(lambda request: httpx.Response(
        200, json={"error": {"message": "model not found"}}))
    original = httpx.Client
    try:
        httpx.Client = lambda **kw: original(transport=handler, **kw)
        with pytest.raises(AIGatewayError) as exc:
            OpenAICompatClient().embeddings(["a"], _cfg(embedding_model="e3"))
        assert exc.value.kind == AIGatewayError.PROTOCOL
    finally:
        httpx.Client = original


def test_embeddings_client_requires_configured_model():
    from WisePet.infra.openai_client import OpenAICompatClient
    client = OpenAICompatClient()
    with pytest.raises(AIGatewayError):
        client.embeddings(["a"], AIConfig(api_key="k", embedding_model=""))
    with pytest.raises(AIGatewayError):
        client.embeddings(["a"], AIConfig(api_key="", embedding_model="e3"))


def test_complete_roundtrip_on_real_client():
    """v1.30 曾因 complete 委托漏传 tools、替身无 complete 而真机全挂测试全绿——
    用真实客户端 + MockTransport 锁死这条链路。"""
    from WisePet.infra.openai_client import OpenAICompatClient
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": "收到"}}]})

    original = httpx.Client
    try:
        httpx.Client = lambda **kw: original(transport=httpx.MockTransport(handler),
                                             **kw)
        text = OpenAICompatClient().complete(
            [{"role": "user", "content": "回复两个字：收到"}], _cfg())
    finally:
        httpx.Client = original
    assert text == "收到"
    assert "tools" not in seen["payload"]      # complete 不携带 tools 字段


# ---------- 6. ChatService 摘要集成 ----------

def test_chat_service_injects_summary_prefix(tmp_path):
    gateway = FakeGatewayWithComplete(chunks=["好", "呀"])
    bus, collected = make_collecting_bus()
    observed = []
    prefixes = ["今天早些时候的对话摘要：聊过周报。"]
    service = ChatService(
        gateway=gateway, session=ChatSession(), bus=bus,
        config_provider=lambda: _cfg(),
        system_prompt_provider=lambda: "你是桌面宠物。",
        local_responder=None,
        summary_observer=lambda role, text: observed.append((role, text)),
        summary_prefix_provider=lambda: prefixes[0])
    service.send("在吗")
    service.process_next()

    request_messages = gateway.calls[0][0]
    assert request_messages[1]["role"] == "system" and "摘要" in request_messages[1]["content"]
    assert observed == [("user", "在吗"), ("assistant", "好呀")]


def test_chat_service_no_prefix_when_summary_empty(tmp_path):
    gateway = FakeGatewayWithComplete(chunks=["好"])
    bus, collected = make_collecting_bus()
    service = ChatService(
        gateway=gateway, session=ChatSession(), bus=bus,
        config_provider=lambda: _cfg(),
        system_prompt_provider=lambda: "你是桌面宠物。",
        local_responder=None,
        summary_prefix_provider=lambda: None)
    service.send("在吗")
    service.process_next()
    assert gateway.calls[0][0][1]["role"] != "system" or "摘要" not in \
        str(gateway.calls[0][0][1])


class FakeGatewayWithComplete(FakeGateway):
    def complete(self, messages, cfg):
        return ""


# ---------- 7. 配置 roundtrip ----------

def test_memory_upgrade_config_roundtrip():
    base = AppConfig()
    cfg = replace(
        base,
        ai=replace(base.ai, embedding_model="embedding-3"),
        memory=replace(base.memory, summary_enabled=False),
        assistant=replace(base.assistant, semantic_search=True))
    back = AppConfig.from_dict(cfg.to_dict())
    assert back.ai.embedding_model == "embedding-3"
    assert back.memory.summary_enabled is False
    assert back.assistant.semantic_search is True
