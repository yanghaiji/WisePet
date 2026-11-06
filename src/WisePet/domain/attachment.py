"""聊天附件：图片转 base64 走多模态协议，文本文件读内容进上下文（工具层 M5）。"""
from __future__ import annotations

import base64
import binascii
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: 单张图片上限：base64 后体积约 x1.37，5MB 足够截图
MAX_IMAGE_BYTES = 5 * 1024 * 1024
#: 单个文本文件上限：塞进 prompt 的内容必须节制
MAX_TEXT_BYTES = 100 * 1024
#: 一次消息最多附件数
MAX_ATTACHMENTS = 6

#: 视作文本文件的扩展名（其它一律当二进制拒绝，避免把 zip 塞进 prompt）
TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".py", ".json", ".csv", ".log",
                 ".yaml", ".yml", ".toml", ".ini", ".html", ".css", ".js",
                 ".ts", ".java", ".c", ".cpp", ".h", ".go", ".rs", ".sql", ".bat", ".ps1"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

#: 后缀 → MIME
_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}


@dataclass(frozen=True)
class Attachment:
    """已加载的附件：图片存 base64，文本存解码后的内容。"""

    name: str
    kind: str          # "image" / "text"
    media_type: str    # image/png 等；文本文件统一 text/plain
    data: str          # image → base64；text → 解码后的正文

    @property
    def data_uri(self) -> str:
        """OpenAI 兼容多模态格式的 data URL（仅图片有意义）。"""
        return f"data:{self.media_type};base64,{self.data}"


def load_attachment(path: str) -> Attachment:
    """读取本地文件为附件；超限/未知二进制类型抛 ValueError（ui 弹气泡提示）。"""
    file_path = os.path.expanduser(str(path or "").strip())
    if not file_path or not os.path.isfile(file_path):
        raise ValueError(f"文件不存在：{file_path}")
    name = os.path.basename(file_path)
    suffix = os.path.splitext(name)[1].lower()
    size = os.path.getsize(file_path)

    if suffix in IMAGE_SUFFIXES:
        if size > MAX_IMAGE_BYTES:
            raise ValueError(f"图片超过 {MAX_IMAGE_BYTES // (1024 * 1024)}MB：{name}")
        with open(file_path, "rb") as fh:
            raw = fh.read()
        try:
            encoded = base64.b64encode(raw).decode("ascii")
            base64.b64decode(encoded, validate=True)
        except (binascii.Error, UnicodeError) as exc:
            raise ValueError(f"图片读取失败：{name}") from exc
        return Attachment(name=name, kind="image",
                          media_type=_MIME.get(suffix, "image/png"), data=encoded)

    if suffix in TEXT_SUFFIXES:
        if size > MAX_TEXT_BYTES:
            raise ValueError(f"文本文件超过 {MAX_TEXT_BYTES // 1024}KB：{name}")
        with open(file_path, "rb") as fh:
            raw = fh.read()
        return Attachment(name=name, kind="text", media_type="text/plain",
                          data=raw.decode("utf-8", "replace").replace("\r\n", "\n"))

    raise ValueError(f"暂不支持这种文件类型：{name}")


def attachment_from_png_bytes(raw: bytes, name: str = "粘贴的图片.png") -> Attachment:
    """剪贴板截图 → 图片附件（ui 层把 QImage 编码成 PNG 后调这里）。"""
    if not raw:
        raise ValueError("图片数据为空")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError(f"图片超过 {MAX_IMAGE_BYTES // (1024 * 1024)}MB")
    encoded = base64.b64encode(raw).decode("ascii")
    return Attachment(name=name, kind="image", media_type="image/png", data=encoded)


def build_text_context(attachments, base_text: str) -> str:
    """把文本附件拼进用户消息正文（图片走独立的多模态段，不占这里）。"""
    blocks = [base_text.strip()] if base_text.strip() else []
    for att in attachments:
        if att.kind == "text":
            blocks.append(f"[附件 {att.name}]\n{att.data}")
    return "\n\n".join(blocks)
