"""统一记忆检索：一个问题查遍各类记忆，单源失败不影响整体。"""
from __future__ import annotations

from WisePet.app.unified_memory import UnifiedMemory, MemoryHit


class _Facts:
    def relevant(self, q, top_k=5):
        return [{"key": "养了只猫", "value": "橘猫胖虎", "day": "2026-11-01"}] \
            if "猫" in q else []


class _Chat:
    def search(self, q, limit=5):
        return [type("R", (), {"content": "上周聊了周报", "day": "2026-11-10"})()] \
            if "周报" in q else []


class _Knowledge:
    def search(self, q, top_k=3):
        return [{"text": "项目排期在周五", "path": "plan.md"}] if "项目" in q else []


class _Broken:
    def relevant(self, *a, **k):
        raise RuntimeError("boom")


def test_recall_merges_sources_and_sorts_by_score():
    um = UnifiedMemory(long_term=_Facts(), chat_history=_Chat(), knowledge=_Knowledge())
    hits = um.recall("猫和周报和项目")
    kinds = {h.source for h in hits}
    assert {"fact", "chat", "knowledge"} <= kinds
    assert hits == sorted(hits, key=lambda h: h.score, reverse=True)
    assert all(isinstance(h, MemoryHit) for h in hits)


def test_prompt_context_has_labels_and_no_hits_empty():
    um = UnifiedMemory(long_term=_Facts(), chat_history=_Chat())
    ctx = um.prompt_context("猫")
    assert "长期记忆" in ctx and "橘猫胖虎" in ctx
    assert um.prompt_context("") == ""
    assert um.prompt_context("完全无关") == ""


def test_one_broken_source_does_not_break_others():
    um = UnifiedMemory(long_term=_Broken(), chat_history=_Chat())
    hits = um.recall("周报")
    assert any(h.source == "chat" for h in hits)


def test_limit_caps_results():
    um = UnifiedMemory(long_term=_Facts(), chat_history=_Chat(), knowledge=_Knowledge())
    assert len(um.recall("猫和周报和项目", limit=1)) == 1
