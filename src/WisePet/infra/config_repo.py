"""用户配置仓储：~/.WisePet/config.json（技术方案 §6）。

项目由 ai-bot 改名为 WisePet 后，用户数据目录随之从 ~/.ai-bot 迁到 ~/.WisePet；
启动时的 migrate_legacy_data() 负责把旧数据复制过来，避免丢失已保存的 API Key。
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from ..domain.config_model import DEFAULT_SYSTEM_PROMPT, AppConfig
from .json_store import atomic_write, load_json_or_backup_reset

logger = logging.getLogger(__name__)

CONFIG_FILE_NAME = "config.json"
_DATA_DIR_NAME = ".WisePet"
#: 改名前的用户数据目录；仅供一次性迁移读取，新代码不再写入
_LEGACY_DATA_DIR_NAME = ".ai-bot"


def mask_key(key: str) -> str:
    """api_key 脱敏：日志与 UI 回显专用（AGENT.md 规约 5）。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}***{key[-2:]}"


def default_data_dir() -> Path:
    """敏感配置一律存用户目录，不入项目/不入库（规约例外项，理由见 AGENT.md）。"""
    return Path.home() / _DATA_DIR_NAME


def migrate_legacy_data(data_dir: Path | str | None = None) -> list[str]:
    """把旧 `~/.ai-bot/` 下的数据（含 logs/ 子目录）复制到新目录。

    复制而非移动：旧目录原样保留作备份；新目录已有的文件不覆盖，
    避免重复启动时把用户新改的配置回退成旧值。返回实际复制的相对路径。
    """
    target = Path(data_dir) if data_dir else default_data_dir()
    legacy = Path.home() / _LEGACY_DATA_DIR_NAME
    if not legacy.is_dir():
        return []
    migrated: list[str] = []
    for src in sorted(legacy.rglob("*")):
        if not src.is_file():
            continue
        relative = src.relative_to(legacy)
        dst = target / relative
        if dst.exists():
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        except OSError:
            logger.warning("legacy data copy failed: %s", relative)
            continue
        migrated.append(str(relative))
    return migrated


class JsonConfigRepository:
    def __init__(self, data_dir: Path | str | None = None):
        self._dir = Path(data_dir) if data_dir else default_data_dir()

    @property
    def path(self) -> Path:
        return self._dir / CONFIG_FILE_NAME

    def load(self) -> AppConfig:
        data = load_json_or_backup_reset(self.path)
        if data is None:
            return AppConfig()
        return AppConfig.from_dict(data)

    def save(self, config: AppConfig) -> None:
        atomic_write(self.path, config.to_dict())
        logger.info("config saved to %s (api_key=%s)",
                    self.path, mask_key(config.ai.api_key) or "<empty>")
