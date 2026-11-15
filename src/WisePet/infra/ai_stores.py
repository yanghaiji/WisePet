"""家庭 AI 共享配置与每日用量：全在本机，防孩子过度消费 AI 费用。

- shared_ai.json（全局）：家长配一次 AI（Key 存这里），勾「使用家庭共享」的
  身份直接用它——孩子身份的配置里永远不会有共享 Key；
- ai_usage.json（每身份独立）：按天计数 AI 请求，超过 daily_ai_limit 就回落
  本地小脑并提示，第二天自动恢复。
"""
from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from ..domain.ports import AIConfig
from .json_store import atomic_write, load_json_or_backup_reset

logger = logging.getLogger(__name__)

FILE_NAME = "shared_ai.json"


class SharedAIStore:
    """家长的家庭共享 AI 配置（存全局目录，各身份共用一份）。"""

    def __init__(self, base_dir: Path | str):
        self._path = Path(base_dir) / FILE_NAME

    def load(self) -> AIConfig | None:
        data = load_json_or_backup_reset(self._path) or {}
        if not isinstance(data, dict) or not str(data.get("api_key") or ""):
            return None
        return AIConfig(
            base_url=str(data.get("base_url") or AIConfig.base_url),
            api_key=str(data.get("api_key") or ""),
            model=str(data.get("model") or AIConfig.model),
            temperature=float(data.get("temperature") or AIConfig.temperature),
            timeout_s=float(data.get("timeout_s") or AIConfig.timeout_s),
            embedding_model=str(data.get("embedding_model") or ""))

    def save(self, ai: AIConfig) -> None:
        payload = {"base_url": ai.base_url, "api_key": ai.api_key,
                   "model": ai.model, "temperature": ai.temperature,
                   "timeout_s": ai.timeout_s, "embedding_model": ai.embedding_model}
        try:
            atomic_write(self._path, payload)
            logger.info("shared AI config saved (key masked)")
        except OSError:
            logger.exception("shared ai save failed")

    def clear(self) -> None:
        try:
            if self._path.exists():
                self._path.unlink()
        except OSError:
            logger.exception("shared ai clear failed")


class AIUsageStore:
    """本身份的 AI 请求按天计数（配额用；只记次数不记内容）。"""

    def __init__(self, data_dir: Path | str):
        self._path = Path(data_dir) / "ai_usage.json"

    def _load(self) -> dict:
        data = load_json_or_backup_reset(self._path) or {}
        return data if isinstance(data, dict) else {}

    def today(self, day: str) -> int:
        data = self._load()
        return int(data.get("count") or 0) if str(data.get("day")) == day else 0

    def increment(self, day: str) -> int:
        data = self._load()
        count = self.today(day) + 1
        try:
            atomic_write(self._path, {"day": day, "count": count})
        except OSError:
            logger.exception("ai usage save failed")
        return count
