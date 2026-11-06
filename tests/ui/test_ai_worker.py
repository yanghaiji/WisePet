"""AiWorker 冒烟：串行消费、空闲轮询、异常不退出、取消退出（技术方案 D1）。"""
from __future__ import annotations

import threading

import pytest

from WisePet.app.chat_service import ChatService
from WisePet.app.event_bus import EventBus
from WisePet.domain.chat_session import ChatSession
from WisePet.domain.config_model import AppConfig
from WisePet.domain.events import ChatReplyChunkEvent
from WisePet.ui.ai_worker import IDLE_POLL_MS, AiWorker
from WisePet.ui.qt_bridge import QtEventBridge
from fakes import FakeGateway


class _StubChat:
    """ChatService 替身：记录调用线程，可编排忙/闲/异常。"""

    def __init__(self, busy_times: int = 0, error: Exception | None = None):
        self._busy = busy_times
        self._error = error
        self.threads: list = []
        self.cancelled = False

    def process_next(self) -> bool:
        self.threads.append(threading.get_ident())
        if self._error is not None:
            raise self._error
        if self._busy > 0:
            self._busy -= 1
            return True
        return False

    def cancel_inflight(self) -> None:
        self.cancelled = True


@pytest.fixture
def workers(qtbot):
    """产出 worker 工厂；用例结束后统一取消，避免线程泄漏到下个测试。"""
    created: list[AiWorker] = []

    def _make(chat) -> AiWorker:
        worker = AiWorker(chat)
        created.append(worker)
        return worker

    yield _make
    for worker in created:
        worker.cancel()
        worker.wait(3000)


def should_consume_pending_messages_in_worker_thread(workers, qtbot):
    chat = _StubChat(busy_times=3)
    worker = workers(chat)

    worker.start()
    qtbot.waitUntil(lambda: len(chat.threads) >= 3, timeout=3000)

    assert chat.threads[0] != threading.get_ident()
    assert len(set(chat.threads)) == 1        # 同一线程串行消费，不并发打请求


def should_keep_polling_when_queue_idle(workers, qtbot):
    chat = _StubChat(busy_times=1)
    worker = workers(chat)

    worker.start()
    qtbot.waitUntil(lambda: len(chat.threads) >= 1, timeout=3000)
    qtbot.wait(IDLE_POLL_MS * 3)              # 空转若干轮

    assert worker.isRunning()
    assert len(chat.threads) > 1


def should_survive_consumer_exception(workers, qtbot):
    """消费异常只记日志，线程不退出（PRD 可靠性：一次失败不影响后续对话）。"""
    chat = _StubChat(error=RuntimeError("gateway exploded"))
    worker = workers(chat)

    worker.start()
    qtbot.waitUntil(lambda: len(chat.threads) >= 2, timeout=3000)

    assert worker.isRunning()


def should_cancel_inflight_and_finish_when_stopped(workers, qtbot):
    chat = _StubChat()
    worker = workers(chat)
    worker.start()
    qtbot.waitUntil(worker.isRunning, timeout=1000)

    worker.cancel()

    qtbot.waitUntil(lambda: chat.cancelled, timeout=1000)
    assert worker.wait(3000)
    assert not worker.isRunning()


def should_deliver_stream_reply_on_main_thread_when_gateway_streams(qtbot, workers):
    """端到端：worker 里的 httpx 同步流 → EventBus → 桥 → 主线程收流式分片。"""
    bus = EventBus()
    bridge = QtEventBridge(bus)
    main_thread = threading.get_ident()
    received: list = []
    bridge.event.connect(lambda ev: received.append((ev, threading.get_ident())))
    chat = ChatService(gateway=FakeGateway(chunks=["你", "好"]),
                       session=ChatSession(max_turns=5), bus=bus,
                       config_provider=lambda: AppConfig().ai,
                       system_prompt_provider=lambda: "你是桌宠")
    worker = workers(chat)

    worker.start()
    assert chat.send("你好")
    chunks = lambda: [ev for ev, _ in received if isinstance(ev, ChatReplyChunkEvent)]
    qtbot.waitUntil(lambda: any(ev.done for ev in chunks()), timeout=5000)

    assert [ev.text_delta for ev in chunks() if not ev.done] == ["你", "好"]
    assert [ev.reply for ev in chunks() if ev.done] == ["你好"]
    assert all(thread == main_thread for _, thread in received)


def should_record_assistant_history_when_reply_finished(qtbot, workers):
    bus = EventBus()
    session = ChatSession(max_turns=5)
    chat = ChatService(gateway=FakeGateway(chunks=["收到"]), session=session, bus=bus,
                       config_provider=lambda: AppConfig().ai,
                       system_prompt_provider=lambda: "你是桌宠")
    worker = workers(chat)

    worker.start()
    chat.send("在吗")
    qtbot.waitUntil(lambda: session.last_role == "assistant", timeout=5000)

    assert session.turns[-1].content == "收到"
