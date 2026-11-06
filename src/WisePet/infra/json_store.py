"""JSON 文件存储公共工具：原子写、损坏备份、文件权限收紧。"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_TMP_SUFFIX = ".tmp"
_POSIX_MODE = 0o600


def atomic_write(path: Path, payload: dict) -> None:
    """先写临时文件再 os.replace，保证不出现半截 JSON（临时文件与目标同目录）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + _TMP_SUFFIX)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)  # replace 会移除 tmp，正常路径无残留
    _tighten_permissions(path)


def load_json_or_backup_reset(path: Path) -> dict | None:
    """读取 JSON；解析失败时把损坏文件改名为 {name}.bak-{ts} 并返回 None。"""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
        logger.warning("json file %s is not an object, resetting", path)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        logger.warning("failed to parse %s: %s, resetting to defaults", path, exc)
    _backup(path)
    return None


def _backup(path: Path) -> None:
    backup = path.with_name(f"{path.name}.bak-{int(time.time())}")
    try:
        shutil.move(str(path), str(backup))
    except OSError:
        logger.exception("failed to backup corrupted file %s", path)


def _tighten_permissions(path: Path) -> None:
    """POSIX 下收紧为 0600；Windows ACL 不适用，尽力而为（PRD D6）。"""
    if os.name == "posix":
        try:
            os.chmod(path, _POSIX_MODE)
        except OSError:
            logger.warning("chmod failed for %s", path)
