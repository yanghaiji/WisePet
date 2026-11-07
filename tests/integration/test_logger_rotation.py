"""日志治理：按等级分档 / 单文件 10MB 分割 / 只保留近 3 天（FR v1.23 C40）。"""
from __future__ import annotations

import logging
import os
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from WisePet.infra import logger_setup as ls
from WisePet.domain.config_model import SystemSettings
from WisePet.infra.logger_setup import (
    ERROR_LOG_FILE_NAME, LOG_DIR_NAME, LOG_FILE_NAME, MAX_BYTES, RETENTION_DAYS,
    DatedRotatingFileHandler, reconfigure_logging, setup_logging,
)

NOW = 1_800_000_000.0
DAY = 86400.0


@pytest.fixture
def clean_root():
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    root.handlers.clear()
    yield root
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    for handler in saved_handlers:
        root.addHandler(handler)
    root.setLevel(saved_level)


def _log_dir(tmp_path):
    return tmp_path / LOG_DIR_NAME


def _flush(root):
    for handler in root.handlers:
        handler.flush()


def _new_handler(base, *, now_provider=None, max_bytes=10 * 1024 * 1024,
                 retention_days=RETENTION_DAYS):
    return DatedRotatingFileHandler(
        base, max_bytes=max_bytes, backup_count=ls.BACKUP_COUNT,
        retention_days=retention_days, now_provider=now_provider)


# ---------- 分级 ----------

def should_split_warning_and_error_into_a_dedicated_file(clean_root, tmp_path):
    setup_logging(tmp_path)
    log = logging.getLogger("WisePet.test")
    log.info("普通信息")
    log.warning("告警内容")
    log.error("错误内容")
    _flush(clean_root)

    main = (_log_dir(tmp_path) / LOG_FILE_NAME).read_text(encoding="utf-8")
    errors = (_log_dir(tmp_path) / ERROR_LOG_FILE_NAME).read_text(encoding="utf-8")

    assert all(text in main for text in ("普通信息", "告警内容", "错误内容"))
    assert "普通信息" not in errors
    assert "告警内容" in errors and "错误内容" in errors


def should_keep_repeated_setup_from_stacking_handlers(clean_root, tmp_path):
    setup_logging(tmp_path)
    before = list(clean_root.handlers)

    setup_logging(tmp_path)

    assert clean_root.handlers == before
    rotating = [h for h in before if isinstance(h, RotatingFileHandler)]
    assert len(rotating) == 2                       # 主日志档 + 错误档
    assert sorted({h.level for h in rotating}) == [logging.INFO, logging.WARNING]


# ---------- 10MB 分割 ----------

def should_use_ten_megabytes_as_the_rollover_threshold():
    assert MAX_BYTES == 10 * 1024 * 1024


def should_rollover_when_single_file_exceeds_threshold(tmp_path):
    """阈值缩到 300B 便于真触发一次滚动；配置层的 MiB 值由下一组用例验证。"""
    _log_dir(tmp_path).mkdir(parents=True, exist_ok=True)
    base = str(_log_dir(tmp_path) / LOG_FILE_NAME)
    handler = _new_handler(base, max_bytes=300)
    handler.setFormatter(logging.Formatter("%(message)s"))

    for index in range(15):
        record = logging.LogRecord(name="WisePet.test", level=logging.INFO,
                                   pathname=__file__, lineno=1,
                                   msg=f"一条足够长的日志 {index:02d} " + "x" * 40,
                                   args=(), exc_info=None)
        handler.emit(record)
    handler.flush()

    assert Path(base + ".1").is_file()
    assert Path(base).is_file()
    handler.close()


def should_apply_system_settings_to_log_handlers(clean_root, tmp_path):
    system = SystemSettings(log_level="warning", log_max_mb=25,
                            log_retention_days=7, log_split_by_level=False)
    setup_logging(tmp_path, system=system)

    handlers = [h for h in clean_root.handlers
                if isinstance(h, DatedRotatingFileHandler)]
    assert len(handlers) == 1                       # 关掉分档就没有错误档
    assert handlers[0].maxBytes == 25 * 1024 * 1024
    assert handlers[0].retention_days == 7
    assert clean_root.level == logging.WARNING


def should_reconfigure_logging_hot_without_stacking_handlers(clean_root, tmp_path):
    setup_logging(tmp_path, system=SystemSettings())
    before = len(clean_root.handlers)

    path = reconfigure_logging(
        SystemSettings(log_level="error", log_max_mb=5,
                       log_retention_days=1, log_split_by_level=False),
        data_dir=tmp_path)

    handlers = [h for h in clean_root.handlers
                if isinstance(h, DatedRotatingFileHandler)]
    assert len(handlers) == 1
    assert len(clean_root.handlers) == before - 1   # 移除旧两档 + 控制台，重挂一档 + 控制台
    assert handlers[0].maxBytes == 5 * 1024 * 1024
    assert handlers[0].retention_days == 1
    assert clean_root.level == logging.ERROR
    assert path == _log_dir(tmp_path) / LOG_FILE_NAME


# ---------- 近 3 天保留 ----------

def _backup(base: str, suffix: int, age_days: float, now: float, content="x"):
    path = Path(f"{base}.{suffix}")
    path.write_text(content, encoding="utf-8")
    stamp = now - age_days * DAY
    os.utime(path, (stamp, stamp))
    return path


def should_delete_backups_older_than_three_days(tmp_path):
    base = str(_log_dir(tmp_path) / LOG_FILE_NAME)
    _log_dir(tmp_path).mkdir(parents=True, exist_ok=True)
    old = _backup(base, 1, 5.0, NOW)
    older = _backup(base, 2, 30.0, NOW)
    recent = _backup(base, 3, 1.0, NOW)
    edge = _backup(base, 4, 2.9, NOW)

    handler = _new_handler(base, now_provider=lambda: NOW)

    assert not old.exists() and not older.exists()
    assert recent.exists() and edge.exists()
    handler.close()


def should_never_delete_the_active_log_file(tmp_path):
    _log_dir(tmp_path).mkdir(parents=True, exist_ok=True)
    base = _log_dir(tmp_path) / LOG_FILE_NAME
    base.write_text("正在写入", encoding="utf-8")
    os.utime(base, (NOW - 30 * DAY, NOW - 30 * DAY))

    handler = _new_handler(str(base), now_provider=lambda: NOW)

    assert base.exists() and base.read_text(encoding="utf-8") == "正在写入"
    handler.close()


def should_purge_newly_expired_backups_without_a_rollover(tmp_path):
    _log_dir(tmp_path).mkdir(parents=True, exist_ok=True)
    base = str(_log_dir(tmp_path) / LOG_FILE_NAME)
    handler = _new_handler(base, now_provider=lambda: NOW)
    stale = _backup(base, 7, 4.0, NOW)

    handler.purge_expired()

    assert not stale.exists()
    handler.close()


def _emit_to(handler, message: str) -> None:
    record = logging.LogRecord(name="WisePet.test", level=logging.INFO,
                               pathname=__file__, lineno=1, msg=message,
                               args=(), exc_info=None)
    handler.emit(record)
    handler.flush()


def should_seal_each_day_and_drop_content_older_than_three_days(tmp_path):
    """不到 10MB 也要跨天封存，否则旧日志赖在「当前文件」里永远删不掉。"""
    _log_dir(tmp_path).mkdir(parents=True, exist_ok=True)
    base = str(_log_dir(tmp_path) / LOG_FILE_NAME)
    clock = [time.time()]
    handler = _new_handler(base, now_provider=lambda: clock[0])
    handler.setFormatter(logging.Formatter("%(message)s"))

    _emit_to(handler, "昨天的日志")
    yesterday = clock[0]
    os.utime(base, (yesterday, yesterday))
    clock[0] = yesterday + DAY
    _emit_to(handler, "今天的日志")

    sealed = Path(base + ".1")
    assert "昨天的日志" in sealed.read_text(encoding="utf-8")
    assert "昨天的日志" not in Path(base).read_text(encoding="utf-8")
    assert "今天的日志" in Path(base).read_text(encoding="utf-8")

    clock[0] = yesterday + 5 * DAY
    _emit_to(handler, "五天后的日志")

    assert "昨天的日志" not in sealed.read_text(encoding="utf-8")
    assert "五天后的日志" in Path(base).read_text(encoding="utf-8")
    handler.close()


def should_keep_each_grades_backups_independent(tmp_path):
    _log_dir(tmp_path).mkdir(parents=True, exist_ok=True)
    main_base = str(_log_dir(tmp_path) / LOG_FILE_NAME)
    error_base = str(_log_dir(tmp_path) / ERROR_LOG_FILE_NAME)
    main_old = _backup(main_base, 1, 9.0, NOW)
    error_old = _backup(error_base, 1, 9.0, NOW)

    main_handler = _new_handler(main_base, now_provider=lambda: NOW)
    error_handler = _new_handler(error_base, now_provider=lambda: NOW)

    assert not main_old.exists() and not error_old.exists()
    main_handler.close()
    error_handler.close()
