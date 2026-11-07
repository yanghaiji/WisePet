"""日志装配：按等级分档 + 单文件 10MB 分割 + 只保留近 3 天，并对 api_key 兜底脱敏。

分级口径：`WisePet.log` 是完整主时间线，`WisePet-error.log` 只收 WARNING 及以上，
排障时不用在海量 INFO 里翻；两档各自按 10MB 滚动、各自按天清理备份。
调用点在 config_repo 已用 mask_key，这里的 Filter 是第二道防线：
任何日志正文里出现的 `sk-xxx` / `Bearer xxx` 一律掩码后再落盘。
"""
from __future__ import annotations

import logging
import re
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable

from .config_repo import default_data_dir, mask_key

LOG_DIR_NAME = "logs"
LOG_FILE_NAME = "WisePet.log"
#: 告警与错误单独成档，方便排障；主日志仍保留完整时间线
ERROR_LOG_FILE_NAME = "WisePet-error.log"
#: 单个日志文件超过 10 MiB 就分割
MAX_BYTES = 10 * 1024 * 1024
#: 只保留近 3 天的滚动备份；当前正在写的文件不参与清理
RETENTION_DAYS = 3
#: 备份数硬上限，仅防极端写入把磁盘撑爆；正常清理按上面的时间口径
BACKUP_COUNT = 200
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


def _backup_paths(base_filename: str) -> list[Path]:
    """只认 RotatingFileHandler 生成的数字后缀备份，不碰当前文件和其它日志档。"""
    path = Path(base_filename)
    return sorted(p for p in path.parent.glob(path.name + ".*")
                  if p.is_file() and p.suffix[1:].isdigit())


class DatedRotatingFileHandler(RotatingFileHandler):
    """按大小滚动 + 按时间保留：超过 retention_days 的备份删掉，与 backupCount 无关。

    `RotatingFileHandler` 只在真正滚动时才清理，若日志量长期不到 10MB，
    旧备份会永远留着；所以 emit 时按自然日再兜一次清理。
    """

    def __init__(self, filename, *, max_bytes: int, backup_count: int,
                 retention_days: float = RETENTION_DAYS, encoding: str = "utf-8",
                 now_provider: Callable[[], float] | None = None):
        super().__init__(filename, maxBytes=max_bytes,
                         backupCount=backup_count, encoding=encoding)
        self.retention_days = float(retention_days)
        self._now = now_provider or time.time
        self._current_day = int(self._now() // 86400.0)
        self._last_purge_day: int | None = None
        self.purge_expired()

    def _expired(self, path: Path) -> bool:
        try:
            age = self._now() - path.stat().st_mtime
        except OSError:
            return False                       # 拿不到时间戳就保守保留
        return age > self.retention_days * 86400.0

    def purge_expired(self) -> int:
        """删除过期的滚动备份（当前正在写的文件从不删）；返回删掉的数量。"""
        removed = 0
        for path in _backup_paths(self.baseFilename):
            if not self._expired(path):
                continue
            try:
                path.unlink()
                removed += 1
            except OSError:
                logging.getLogger(__name__).warning("failed to purge stale log %s",
                                                    path)
        self._last_purge_day = int(self._now() // 86400.0)
        return removed

    def emit(self, record: logging.LogRecord) -> None:
        day = int(self._now() // 86400.0)
        # 先清过期、再跨天封存：反过来会把刚封存的「昨天」当成过期文件删掉。
        if self._last_purge_day != day:
            self.purge_expired()
        if day != self._current_day:
            # 跨天强制封存：否则日志量长期不到 10MB 时，昨天以前的内容会一直
            # 赖在「当前文件」里，按天清理删不到，「只保留近 3 天」就不成立。
            self._current_day = day
            if self.stream is not None:
                self.flush()
                self.doRollover()
        super().emit(record)

    def getFilesToDelete(self) -> list[str]:
        """滚动时的清理也走时间口径，避免 backupCount 误删 3 天内的备份。"""
        return [str(p) for p in _backup_paths(self.baseFilename)
                if self._expired(p)]


_LEVELS = {"debug": logging.DEBUG, "info": logging.INFO,
           "warning": logging.WARNING, "error": logging.ERROR}


def _effective_level(system, explicit: int | None) -> int:
    """显式 level 参数优先（后向兼容），否则取系统配置，都没有就 INFO。"""
    if explicit is not None:
        return explicit
    if system is not None:
        return _LEVELS.get(str(system.log_level or "").strip().lower(), logging.INFO)
    return logging.INFO


def setup_logging(data_dir: Path | str | None = None,
                  level: int | None = None, system=None) -> Path:
    """初始化根 logger，返回主日志路径。重复调用不叠加 handler。

    system 为 `SystemSettings`：日志级别 / 单文件 MiB 上限 / 保留天数 / 错误档开关
    都由设置窗「系统配置」分区提供；不传时用模块常量作为默认值（测试与旧调用兼容）。
    """
    directory = Path(data_dir) if data_dir else default_data_dir()
    log_dir = directory / LOG_DIR_NAME
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / LOG_FILE_NAME

    root = logging.getLogger()
    effective_level = _effective_level(system, level)
    root.setLevel(effective_level)
    if any(isinstance(h, DatedRotatingFileHandler) for h in root.handlers):
        return log_file                                    # 幂等：测试/重启场景不重复挂

    max_bytes_mb = system.log_max_mb if system else MAX_BYTES // (1024 * 1024)
    retention = system.log_retention_days if system else RETENTION_DAYS
    split_by_level = system.log_split_by_level if system else True
    max_bytes = int(max_bytes_mb) * 1024 * 1024

    formatter = logging.Formatter(_FORMAT)
    mask = SecretMaskFilter()

    def _handler(path: Path, min_level: int) -> DatedRotatingFileHandler:
        handler = DatedRotatingFileHandler(path, max_bytes=max_bytes,
                                           backup_count=BACKUP_COUNT,
                                           retention_days=retention)
        handler.setLevel(min_level)
        handler.setFormatter(formatter)
        handler.addFilter(mask)
        root.addHandler(handler)
        return handler

    _handler(log_file, effective_level)                    # 主时间线
    if split_by_level:
        _handler(log_dir / ERROR_LOG_FILE_NAME,
                 max(logging.WARNING, effective_level))    # 告警/错误档

    # PyInstaller --windowed 下 sys.stderr 可能为 None，此时只保留文件日志
    if sys.stderr is not None:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        console.addFilter(mask)
        console._wisepet_console = True                   # 热重建时识别自己挂的控制台
        root.addHandler(console)
    return log_file


def reconfigure_logging(system, data_dir: Path | str | None = None) -> Path:
    """系统配置保存后热生效：只替换自己挂的 handler，不动第三方挂的。"""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if (isinstance(handler, DatedRotatingFileHandler)
                or getattr(handler, "_wisepet_console", False)):
            root.removeHandler(handler)
            handler.close()
    return setup_logging(data_dir=data_dir, system=system)
