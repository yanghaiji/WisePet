"""日志装配测试：滚动文件落盘、密钥兜底脱敏、重复调用幂等（AGENT.md 规约 5）。"""
import logging
from logging.handlers import RotatingFileHandler

import pytest

from WisePet.infra.config_repo import mask_key
from WisePet.infra.logger_setup import (
    LOG_DIR_NAME,
    LOG_FILE_NAME,
    SecretMaskFilter,
    mask_secrets,
    setup_logging,
)

_SECRET = "sk-abcdef1234567890"


@pytest.fixture
def clean_root():
    """测试期间接管根 logger，结束后原样归还，避免污染其它用例的输出。"""
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    root.handlers.clear()
    yield root
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()          # 归还前释放本用例挂上的文件句柄（Windows 下避免 tmp 占用）
    for handler in saved_handlers:
        root.addHandler(handler)
    root.setLevel(saved_level)


def _emit(root, logger_name="WisePet.test"):
    logging.getLogger(logger_name).info("已保存 key=%s 的配置", _SECRET)
    for handler in root.handlers:
        handler.flush()


def should_create_log_file_under_data_dir_when_setup(clean_root, tmp_path):
    log_file = setup_logging(tmp_path)

    assert log_file == tmp_path / LOG_DIR_NAME / LOG_FILE_NAME
    assert log_file.parent.is_dir()


def should_write_message_with_level_and_module_when_logged(clean_root, tmp_path):
    log_file = setup_logging(tmp_path)

    _emit(clean_root)
    text = log_file.read_text(encoding="utf-8")

    assert "INFO" in text and "WisePet.test" in text


def should_mask_positional_args_when_logged(clean_root, tmp_path):
    log_file = setup_logging(tmp_path)

    _emit(clean_root)
    text = log_file.read_text(encoding="utf-8")

    assert _SECRET not in text
    assert mask_key(_SECRET) in text


def should_mask_mapping_args_when_logged(clean_root, tmp_path):
    """%(...)s 风格：只脱敏字符串值，映射结构与数值类型必须保留。"""
    log_file = setup_logging(tmp_path)
    # LogRecord 会把单元素元组里的映射展开成 dict，与 logger.warning("%(k)s", {"k": v}) 一致
    record = logging.LogRecord(name="WisePet.test", level=logging.WARNING,
                               pathname=__file__, lineno=1,
                               msg="config %(key)s updated (%(count)d)",
                               args=({"key": _SECRET, "count": 3},), exc_info=None)

    clean_root.handle(record)
    for handler in clean_root.handlers:
        handler.flush()

    text = log_file.read_text(encoding="utf-8")
    assert isinstance(record.args, dict)
    assert record.args["count"] == 3
    assert _SECRET not in text
    assert mask_key(_SECRET) in text
    assert "updated (3)" in text


def should_keep_numeric_arg_type_when_filtered():
    """数值参数不得强转为 str：`%.2f` 收到字符串会让整条日志静默丢失。"""
    record = logging.LogRecord(name="t", level=logging.INFO, pathname=__file__, lineno=1,
                               msg="cold start took %.2fs (target <3s)",
                               args=(0.53,), exc_info=None)

    SecretMaskFilter().filter(record)

    assert record.args == (0.53,)
    assert record.getMessage() == "cold start took 0.53s (target <3s)"


def should_write_formatted_line_when_args_are_numeric(clean_root, tmp_path):
    """端到端：启动耗时这类带数值参数的日志必须真的落盘（真机跑过才发现丢日志）。"""
    log_file = setup_logging(tmp_path)

    logging.getLogger("WisePet.main").info("cold start took %.2fs", 0.42)
    for handler in clean_root.handlers:
        handler.flush()

    assert "cold start took 0.42s" in log_file.read_text(encoding="utf-8")


def should_mask_bearer_token_when_message_rendered():
    record = logging.LogRecord(name="t", level=logging.INFO, pathname=__file__, lineno=1,
                               msg=f"Authorization: Bearer {_SECRET}",
                               args=None, exc_info=None)

    SecretMaskFilter().filter(record)

    assert _SECRET not in record.getMessage()


def should_mask_bare_string_args_when_filtered():
    """非常规构造（args 直接是字符串）也不能漏脱敏。"""
    record = logging.LogRecord(name="t", level=logging.INFO, pathname=__file__, lineno=1,
                               msg="cached %s", args=_SECRET, exc_info=None)

    SecretMaskFilter().filter(record)

    assert _SECRET not in record.getMessage()
    assert mask_key(_SECRET) in record.getMessage()


@pytest.mark.parametrize("payload", [
    [_SECRET, 7],
    {"nested": {"key": _SECRET}},
    (_SECRET,),
])
def should_mask_secret_inside_container_when_filtered(payload):
    """密钥藏在列表/嵌套映射里也要被掩掉（纵深防御，不依赖调用点自觉）。"""
    record = logging.LogRecord(name="t", level=logging.INFO, pathname=__file__, lineno=1,
                               msg="dump=%s count=%s", args=(payload, 7), exc_info=None)

    SecretMaskFilter().filter(record)

    text = record.getMessage()
    assert _SECRET not in text
    assert mask_key(_SECRET) in text
    assert "count=7" in text          # 数值参数类型不变，格式化照旧


def should_keep_single_file_handler_when_setup_called_twice(clean_root, tmp_path):
    setup_logging(tmp_path)

    second = setup_logging(tmp_path)

    # 分级后是主日志档 + 错误档两个 RollingHandler，但重复调用不叠加
    rotating = [h for h in clean_root.handlers if isinstance(h, RotatingFileHandler)]
    assert len(rotating) == 2
    assert second == tmp_path / LOG_DIR_NAME / LOG_FILE_NAME


def should_leave_text_untouched_when_no_secret_present():
    assert mask_secrets("普通日志：走动速度 60px/s") == "普通日志：走动速度 60px/s"
    assert mask_secrets("") == ""


def should_mask_short_key_as_full_block_when_too_short():
    assert mask_secrets("sk-abcd") == "***"
