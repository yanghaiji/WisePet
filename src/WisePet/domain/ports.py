"""领域端口（typing.Protocol）：由 infra 层实现，domain/app 只依赖接口。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Protocol, runtime_checkable


@dataclass(frozen=True)
class AIConfig:
    """AI 后端接入参数（OpenAI 兼容）。"""

    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    temperature: float = 0.8
    timeout_s: float = 30.0

    @property
    def chat_endpoint(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    @property
    def configured(self) -> bool:
        return bool(self.api_key.strip())


@runtime_checkable
class Clock(Protocol):
    """可注入时钟，便于测试。"""

    def now(self) -> float: ...

    def monotonic(self) -> float: ...


@runtime_checkable
class AIChatGateway(Protocol):
    """流式对话网关：逐段 yield 增量文本；失败抛 AIGatewayError。"""

    def stream_chat(self, messages: list[dict], cfg: AIConfig) -> Iterator[str]: ...


@runtime_checkable
class FullscreenDetector(Protocol):
    """全屏应用检测（勿扰判定）；无法检测的平台返回 False。"""

    def is_user_fullscreen(self) -> bool: ...
