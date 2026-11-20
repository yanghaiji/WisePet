"""领域端口（typing.Protocol）：由 infra 层实现，domain/app 只依赖接口。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Protocol, runtime_checkable


#: 模型用途：chat=对话、image=画图。默认 chat 保证旧配置/测试零迁移。
PURPOSE_CHAT = "chat"
PURPOSE_IMAGE = "image"
PURPOSE_VIDEO = "video"
PURPOSES = (PURPOSE_CHAT, PURPOSE_IMAGE, PURPOSE_VIDEO)


@dataclass(frozen=True)
class AIConfig:
    """AI 后端接入参数（OpenAI 兼容）。

    purpose 决定这个方案被用在哪：chat 走对话、image 走画图。
    不同用途可在设置里各配一个方案，聊天与画图各用各的模型。
    """

    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    temperature: float = 0.8
    timeout_s: float = 30.0
    embedding_model: str = ""   # 留空 = 不启用语义检索（资料索引纯关键词）
    purpose: str = PURPOSE_CHAT

    @property
    def chat_endpoint(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    @property
    def embedding_endpoint(self) -> str:
        return self.base_url.rstrip("/") + "/embeddings"

    @property
    def image_endpoint(self) -> str:
        return self.base_url.rstrip("/") + "/images/generations"

    @property
    def video_endpoint(self) -> str:
        """创建视频任务：OpenAI Videos 兼容的 /v1/videos。"""
        return self.base_url.rstrip("/") + "/videos"

    @property
    def video_status_endpoint(self) -> str:
        """查询视频任务状态：Agnes 在 host 根路径 /agnesapi（不在 /v1 下）。"""
        from urllib.parse import urlsplit
        parts = urlsplit(self.base_url)
        return f"{parts.scheme}://{parts.netloc}/agnesapi"

    @property
    def configured(self) -> bool:
        return bool(self.api_key.strip())

    @property
    def semantic_ready(self) -> bool:
        return bool(self.api_key.strip() and self.embedding_model.strip())


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
class ImageGenerationGateway(Protocol):
    """图像生成网关：按 prompt 生成图片，返回 (图片字节, 媒体类型)。失败抛 AIGatewayError。"""

    def generate_image(self, prompt: str, cfg: AIConfig,
                       size: str = "1024x1024") -> tuple[bytes, str]: ...


@runtime_checkable
class VideoGenerationGateway(Protocol):
    """视频生成网关（异步）：建任务→轮询→下载，返回 (视频字节, 扩展名)。

    通过 progress 回调把 "排队中/生成中 40%" 之类的进度送到 UI；失败抛 AIGatewayError。
    """

    def generate_video(self, prompt: str, cfg: AIConfig,
                       progress=None) -> tuple[bytes, str]: ...


@runtime_checkable
class FullscreenDetector(Protocol):
    """全屏应用检测（勿扰判定）；无法检测的平台返回 False。"""

    def is_user_fullscreen(self) -> bool: ...
