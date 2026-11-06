"""日志装配：控制台 + 滚动文件，并对 api_key 兜底脱敏（PRD NFR 可靠性 / AGENT.md 规约 5）。

调用点在 config_repo 已用 mask_key，这里的 Filter 是第二道防线：
任何日志正文里出现的 `sk-xxx` / `Bearer xxx` 一律掩码后再落盘。
"""
from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config_repo import default_data_dir, mask_key

LOG_DIR_NAME = "logs"
LOG_FILE_NAME = "WisePet.log"
#: 单文件 1MB × 3 份备份，桌宠常驻也不至于撑爆磁盘
MAX_BYTES = 1_000_000
BACKUP_COUNT = 3
_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"

_SK_TOKEN = re.compile(r"sk-[A-Za-z0-9_\-]{4,}")
_BEARER = re.compile(r"(?i)(bearer\s+)(\S+)")


def mask_secrets(text: str) -> str:
    """把日志文本中的密钥类片段替换成脱敏形式（纯函数便于单测）。"""
    masked = _SK_TOKEN.sub(lambda m: mask_key(m.group()), text)
    return _BEARER.sub(lambda m: f"{m.group(1)}{mask_key(m.group(2))}", masked)


class SecretMaskFilter(logging.Filter):
    """对 record 的正文与参数做脱敏，异常堆栈本身不含密钥原文。

    只替换字符串内容：把数值/路径等参数强转成 str 会让 `%` 格式化失败
    （`%.2f` 收到字符串直接 TypeError），后果是整条日志被静默丢弃。
    容器参数（list/tuple/dict）递归到字符串叶子，以免密钥藏在列表/映射里漏脱敏。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = mask_secrets(record.msg)
        # 兼容三种参数形态：位置元组、%(...)s 风格映射、以及直接传入的单值
        if isinstance(record.args, (tuple, list, dict, str)):
            record.args = _mask_value(record.args)
        return True


def _mask_value(value: object) -> object:
    """只替换字符串（含嵌套容器内的字符串），其余类型原样保留以保证 `%` 格式化行为不变。"""
    if isinstance(value, str):
        return mask_secrets(value)
    if isinstance(value, dict):
        return {k: _mask_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_mask_value(v) for v in value)
    return value


def setup_logging(data_dir: Path | str | None = None,
                  level: int = logging.INFO) -> Path:
    """初始化根 logger，返回日志文件路径。重复调用不叠加 handler。"""
    directory = Path(data_dir) if data_dir else default_data_dir()
    log_dir = directory / LOG_DIR_NAME
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / LOG_FILE_NAME

    root = logging.getLogger()
    root.setLevel(level)
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return log_file                                    # 幂等：测试/重启场景不重复挂

    formatter = logging.Formatter(_FORMAT)
    mask = SecretMaskFilter()

    file_handler = RotatingFileHandler(log_file, maxBytes=MAX_BYTES,
                                       backupCount=BACKUP_COUNT, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.addFilter(mask)
    root.addHandler(file_handler)

    # PyInstaller --windowed 下 sys.stderr 可能为 None，此时只保留文件日志
    if sys.stderr is not None:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        console.addFilter(mask)
        root.addHandler(console)
    return log_file
