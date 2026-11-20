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
#: 固定位置的「数据目录指针」：存自定义根路径。放在默认目录里，启动时先读它
#: 才知道 config.json 在哪（避免「config 在自己内部指定自己位置」的先有鸡问题）。
LOCATION_FILE_NAME = "location.json"
#: 环境变量覆盖（优先级最高，便于命令行/排障）
DATA_DIR_ENV = "WISEPET_DATA_DIR"


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


def location_file() -> Path:
    """指针文件固定放在默认目录，不随自定义位置移动。"""
    return default_data_dir() / LOCATION_FILE_NAME


def resolve_base_dir() -> Path:
    """解析数据根目录：环境变量 > 指针文件 > 默认目录。

    指针文件位于固定位置，内容是 {"path": "自定义绝对路径"}；路径不存在或不可写时
    安全回落默认目录，绝不因为坏指针让程序起不来。
    """
    import os
    env = str(os.environ.get(DATA_DIR_ENV) or "").strip()
    if env:
        candidate = Path(env).expanduser()
        if _usable_dir(candidate):
            return candidate
        logger.warning("env %s points to unusable dir: %s", DATA_DIR_ENV, candidate)
    pointer = location_file()
    data = load_json_or_backup_reset(pointer)
    if isinstance(data, dict):
        raw = str(data.get("path") or "").strip()
        if raw:
            candidate = Path(raw).expanduser()
            if _usable_dir(candidate):
                return candidate
            logger.warning("data dir pointer points to unusable dir: %s", candidate)
    return default_data_dir()


def _usable_dir(path: Path) -> bool:
    """路径可用：绝对路径、是已存在目录、可读写（尝试建一个探针文件验证写权限）。"""
    try:
        if not path.is_absolute() or not path.is_dir():
            return False
        probe = path / ".wisepet-write-test"
        probe.write_text("", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def save_data_dir(path: Path | str) -> bool:
    """把自定义根路径写进固定位置的指针文件；成功返回 True。"""
    raw = str(path or "").strip()
    if not raw:
        return False
    candidate = Path(raw).expanduser()
    if not _usable_dir(candidate):
        return False
    try:
        location_file().parent.mkdir(parents=True, exist_ok=True)
        atomic_write(location_file(), {"path": str(candidate.resolve())})
        return True
    except OSError:
        logger.exception("save data dir pointer failed")
        return False


def migrate_data(source: Path | str, target: Path | str) -> list[str]:
    """把 source 下的数据复制到 target（不覆盖同名、保留源作备份）。

    与 migrate_legacy_data 同一策略，但源可任意指定——用于「旧位置 → 新位置」。
    """
    src = Path(source)
    dst = Path(target)
    if not src.is_dir():
        return []
    migrated: list[str] = []
    for item in sorted(src.rglob("*")):
        if not item.is_file():
            continue
        relative = item.relative_to(src)
        out = dst / relative
        if out.exists():
            continue
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, out)
        except OSError:
            logger.warning("data copy failed: %s", relative)
            continue
        migrated.append(str(relative))
    return migrated


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
