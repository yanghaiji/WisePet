"""app 层测试共享替身：假时钟/假网关/事件收集。"""
from WisePet.app.event_bus import EventBus

#: 固定基准时间（脱离时区干扰由测试自行 advance）
BASE_TS = 1_700_000_000.0


class FakeClock:
    def __init__(self, now: float = BASE_TS):
        self._now = now
        self._mono = 0.0

    def now(self) -> float:
        return self._now

    def monotonic(self) -> float:
        return self._mono

    def advance(self, dt: float) -> None:
        self._now += dt
        self._mono += dt


class FakeGateway:
    """记录调用参数的流式网关替身（error 可为任意异常，用于验证兜底）。"""

    def __init__(self, chunks=(), error: Exception | None = None,
                 before_first_chunk=None):
        self._chunks = list(chunks)
        self._error = error
        self.before_first_chunk = before_first_chunk
        self.calls: list[tuple] = []

    def stream_chat(self, messages, cfg):
        self.calls.append((messages, cfg))
        if self._error is not None:
            raise self._error
        if self.before_first_chunk is not None:
            self.before_first_chunk()
        for chunk in self._chunks:
            yield chunk


class FakeFullscreenDetector:
    def __init__(self, fullscreen: bool = False):
        self.fullscreen = fullscreen

    def is_user_fullscreen(self) -> bool:
        return self.fullscreen


class FakeResponder:
    """本地小脑替身：固定回复并记录收到的用户文本（避开真实话术）。"""

    def __init__(self, reply: str = "喵～", intent: str = "greet"):
        self.fixed = reply
        self.texts: list[str] = []
        #: 与真 LocalResponder 同形的诊断字段，ChatService 的日志会读它
        self.last_intent = ""
        self._intent = intent

    def reply(self, text: str) -> str:
        self.texts.append(text)
        self.last_intent = self._intent
        return self.fixed


class StubRng:
    """固定值随机源：让概率分支（是否小跑/是否收趟/间隔多长）可断言。

    不再依赖 `random.Random(42)` 的**调用顺序运气**：加一个概率判断就会让
    后面的 choice/uniform 拿到不同的值，旧用例就是这么被带偏的。

    - ``random()``：依次取 ``values``，取完回落到 ``always``（默认 0.99 = 概率分支都不命中）
    - ``choice(seq)``：永远取首个（默认方向 -1，即向左走）
    - ``uniform(a, b)``：一律取上限 b（测试里把闲置间隔推到很大就能抑住自主行为）
    """

    def __init__(self, values=(), always: float = 0.99):
        self._values = list(values)
        self._always = always

    def random(self) -> float:
        return self._values.pop(0) if self._values else self._always

    def choice(self, seq):
        return seq[0]

    def randint(self, a: int, b: int) -> int:
        """固定取区间中点：瞬移目标可断言且必落在界内。"""
        return (a + b) // 2

    def uniform(self, a: float, b: float) -> float:
        return b


def make_collecting_bus() -> tuple[EventBus, list]:
    """返回 (bus, collected)：通配订阅收集所有事件。"""
    bus = EventBus()
    collected: list = []
    bus.subscribe(None, collected.append)
    return bus, collected
