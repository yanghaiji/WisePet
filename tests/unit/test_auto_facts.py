"""通用事实记忆：AI 抽取任意话题的事实，不依赖固定句式正则。"""
from __future__ import annotations

import time

from WisePet.app.auto_facts import AutoFactExtractor, parse_facts
from WisePet.app.long_term_memory import LongTermMemory
from WisePet.infra.product_store import MemoryStore


class _Gateway:
    def __init__(self, reply='[{"key": "名字", "value": "a"}]'):
        self.reply = reply
        self.calls = []

    def complete(self, messages, cfg):
        self.calls.append(messages)
        return self.reply


def _memory(tmp_path, enabled=True):
    store = MemoryStore(tmp_path)
    return LongTermMemory(store, lambda: enabled), store


def _extractor(memory, gateway, enabled=True, buffer_limit=8):
    return AutoFactExtractor(memory, gateway, lambda: object(),
                             lambda: enabled, buffer_limit=buffer_limit)


class TestParseFacts:
    def should_parse_plain_json(self):
        assert parse_facts('[{"key":"城市","value":"长春"}]') == [("城市", "长春")]

    def should_tolerate_markdown_fence_and_prose(self):
        raw = '好的：\n```json\n[{"key":"喜好","value":"喝红茶"}]\n```\n以上'
        assert parse_facts(raw) == [("喜好", "喝红茶")]

    def should_return_empty_on_broken_or_non_fact_output(self):
        assert parse_facts("抱歉我不能回答") == []
        assert parse_facts("[]") == []
        assert parse_facts('[{"key":"x"},{"value":"y"}]') == []
        assert parse_facts('{"key":"x","value":"y"}') == []


class TestExtraction:
    def should_extract_arbitrary_topic_fact_from_short_user_message(self, tmp_path):
        """换任何话题/说法都行：事实内容由 AI 判断并落盘。"""
        memory, store = _memory(tmp_path)
        gateway = _Gateway('[{"key":"常去","value":"一汽35街区"},'
                           '{"key":"忌口","value":"不吃香菜"}]')
        extractor = _extractor(memory, gateway)
        extractor.observe("user", "我平时都在一汽35街区晃，不吃香菜")
        time.sleep(0.4)                        # 后台线程
        assert memory.entries()
        values = {e["value"] for e in memory.entries()}
        assert {"一汽35街区", "不吃香菜"} <= values
        assert gateway.calls
        # 跨重启可读
        assert LongTermMemory(store, lambda: True).entries()

    def should_not_extract_on_question(self, tmp_path):
        memory, _ = _memory(tmp_path)
        gateway = _Gateway()
        extractor = _extractor(memory, gateway)
        extractor.observe("user", "我叫什么来着？")
        time.sleep(0.2)
        assert not gateway.calls                 # 问句不触发立即抽取

    def should_flush_pending_buffer_when_below_limit(self, tmp_path):
        memory, _ = _memory(tmp_path)
        gateway = _Gateway('[{"key":"宠物","value":"养了只橘猫"}]')
        extractor = _extractor(memory, gateway)
        for i in range(3):
            extractor.observe("assistant", f"闲聊{i}")
        extractor.flush(wait_s=1.0)
        assert any(e["value"] == "养了只橘猫" for e in memory.entries())

    def should_deduplicate_already_known_facts(self, tmp_path):
        memory, _ = _memory(tmp_path)
        memory.remember("名字", "a")
        before = len(memory.timeline())
        gateway = _Gateway('[{"key":"名字","value":"a"}]')
        extractor = _extractor(memory, gateway)
        extractor.observe("user", "我叫a")
        time.sleep(0.4)
        assert len([e for e in memory.entries() if e["key"] == "名字"]) == 1
        assert len(memory.timeline()) == before    # 没重复写时间线

    def should_stay_silent_when_disabled_or_gateway_fails(self, tmp_path):
        memory, _ = _memory(tmp_path)
        gateway = _Gateway("not json")
        extractor = _extractor(memory, gateway, enabled=False)
        extractor.observe("user", "我叫a")
        assert not gateway.calls
        enabled = _extractor(memory, gateway, enabled=True)
        enabled.observe("user", "我叫a")
        time.sleep(0.3)
        assert not memory.entries()            # 坏输出静默，不崩
