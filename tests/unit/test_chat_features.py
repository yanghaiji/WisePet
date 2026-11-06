"""新功能测试：附件/多模态消息、多模型方案配置与切换。"""
import json

import pytest

from WisePet.app.chat_service import ChatService
from WisePet.domain.attachment import (
    MAX_IMAGE_BYTES,
    Attachment,
    build_text_context,
    load_attachment,
)
from WisePet.domain.config_model import AIProfile, AppConfig
from WisePet.domain.events import ChatReplyChunkEvent
from fakes import FakeClock


# ---------- 附件 ----------

class TestAttachment:
    def should_load_text_file(self, tmp_path):
        target = tmp_path / "note.md"
        target.write_text("# 标题\n内容", encoding="utf-8")

        att = load_attachment(str(target))

        assert att.kind == "text" and att.data == "# 标题\n内容"
        assert att.name == "note.md"

    def should_load_image_as_data_uri(self, tmp_path):
        target = tmp_path / "pic.png"
        target.write_bytes(b"\x89PNG fake-bytes")

        att = load_attachment(str(target))

        assert att.kind == "image" and att.media_type == "image/png"
        assert att.data_uri.startswith("data:image/png;base64,")

    def should_reject_missing_or_unknown_files(self, tmp_path):
        with pytest.raises(ValueError):
            load_attachment(str(tmp_path / "nope.txt"))
        blob = tmp_path / "blob.bin"
        blob.write_bytes(b"\x00\x01")
        with pytest.raises(ValueError):
            load_attachment(str(blob))

    def should_reject_oversize_text(self, tmp_path):
        big = tmp_path / "big.log"
        big.write_text("x" * (200 * 1024), encoding="utf-8")
        with pytest.raises(ValueError):
            load_attachment(str(big))

    def should_build_text_context_with_text_files(self):
        atts = (Attachment("a.md", "text", "text/plain", "内容A"),
                Attachment("p.png", "image", "image/png", "AAAA"))
        assert build_text_context(atts, "看看这个") == "看看这个\n\n[附件 a.md]\n内容A"

    def should_build_attachment_from_clipboard_png_bytes(self):
        from WisePet.domain.attachment import attachment_from_png_bytes
        # 1x1 白色 PNG 的完整字节（手工构造的最小合法图交给 ui 层编码，这里验证封装逻辑）
        png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
        att = attachment_from_png_bytes(png_header)
        assert att.kind == "image" and att.name == "粘贴的图片.png"
        assert att.data_uri.startswith("data:image/png;base64,")

    def should_reject_empty_clipboard_bytes(self):
        from WisePet.domain.attachment import attachment_from_png_bytes
        with pytest.raises(ValueError):
            attachment_from_png_bytes(b"")


# ---------- 多模态消息 ----------

class _RecordingGateway:
    def __init__(self):
        self.calls = []

    def stream_chat(self, messages, cfg):
        self.calls.append(messages)
        yield "收到"


def _make_service():
    from WisePet.domain.chat_session import ChatSession
    from WisePet.domain.ports import AIConfig
    from WisePet.app.event_bus import EventBus
    gateway = _RecordingGateway()
    service = ChatService(
        gateway=gateway, session=ChatSession(), bus=EventBus(),
        config_provider=lambda: AIConfig(api_key="sk-test"),
        system_prompt_provider=lambda: "你是桌宠")
    return service, gateway


class TestMultimodalMessage:
    def should_send_image_and_text_parts_when_attachments_present(self):
        service, gateway = _make_service()
        atts = (Attachment("p.png", "image", "image/png", "AAAA"),
                Attachment("a.txt", "text", "text/plain", "文件内容"))

        assert service.send("看看这两样", atts) is True
        service.process_next()

        sent = gateway.calls[0][-1]
        assert sent["role"] == "user"
        parts = sent["content"]
        assert parts[0]["type"] == "text"
        assert "看看这两样" in parts[0]["text"]
        assert "[附件 a.txt]" in parts[0]["text"] and "文件内容" in parts[0]["text"]
        assert parts[1]["type"] == "image_url"
        assert parts[1]["image_url"]["url"] == "data:image/png;base64,AAAA"

    def should_keep_session_history_text_only(self):
        from WisePet.domain.chat_session import ChatSession   # noqa: F401 - 路径校验
        service, _gateway = _make_service()
        atts = (Attachment("a.txt", "text", "text/plain", "文件内容"),)
        service.send("带个文件", atts)
        service.process_next()

        assert service._session.turns[-2].content == "带个文件"   # 上下文仍是纯文本


# ---------- 多模型方案 ----------

class TestAIProfiles:
    def should_upgrade_legacy_config_to_single_profile(self, tmp_path):
        (tmp_path / "config.json").write_text(json.dumps({
            "ai": {"base_url": "https://x/v4", "api_key": "k1", "model": "glm-4-flash"},
        }), encoding="utf-8")

        from WisePet.infra.config_repo import JsonConfigRepository
        cfg = JsonConfigRepository(tmp_path).load()

        assert len(cfg.ai_profiles) == 1
        assert cfg.ai_profiles[0].api_key == "k1"
        assert cfg.ai_profiles[0].name == "默认"

    def should_parse_multiple_profiles_and_keep_active(self, tmp_path):
        (tmp_path / "config.json").write_text(json.dumps({
            "ai": {"base_url": "https://x/v4", "api_key": "k1", "model": "glm-4-flash"},
            "ai_profiles": [
                {"name": "闪速", "base_url": "https://x/v4", "api_key": "k1",
                 "model": "glm-4-flash"},
                {"name": "视觉", "base_url": "https://x/v4", "api_key": "k2",
                 "model": "glm-4v-flash"},
            ],
        }), encoding="utf-8")

        from WisePet.infra.config_repo import JsonConfigRepository
        cfg = JsonConfigRepository(tmp_path).load()

        assert [p.name for p in cfg.ai_profiles] == ["闪速", "视觉"]
        # 存盘后再读回：方案列表不丢
        repo = JsonConfigRepository(tmp_path)
        repo.save(cfg)
        assert [p.name for p in repo.load().ai_profiles] == ["闪速", "视觉"]

    def should_skip_broken_profile_entries(self, tmp_path):
        (tmp_path / "config.json").write_text(json.dumps({
            "ai": {"base_url": "https://x/v4", "api_key": "k1"},
            "ai_profiles": ["junk", {"name": "", "base_url": "https://y"},
                            {"name": "好的", "base_url": "https://y", "model": "m2"}],
        }), encoding="utf-8")

        from WisePet.infra.config_repo import JsonConfigRepository
        cfg = JsonConfigRepository(tmp_path).load()

        # 坏条目被剔除；当前生效 ai 不在方案里 → 自动补录「默认」保底
        assert [p.name for p in cfg.ai_profiles] == ["好的", "默认"]
