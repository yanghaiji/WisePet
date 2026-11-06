"""json_store 工具测试：原子写、非对象载荷降级、备份失败不抛（PRD 可靠性）。"""
import json
import shutil

import pytest

from WisePet.infra.json_store import atomic_write, load_json_or_backup_reset


def should_create_parent_dirs_when_writing(tmp_path):
    path = tmp_path / "nested" / "deep" / "data.json"

    atomic_write(path, {"a": 1})

    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}


def should_keep_utf8_readable_when_writing_chinese(tmp_path):
    path = tmp_path / "cn.json"

    atomic_write(path, {"名字": "小墨"})

    assert "小墨" in path.read_text(encoding="utf-8")


def should_return_none_when_file_absent(tmp_path):
    assert load_json_or_backup_reset(tmp_path / "nope.json") is None


@pytest.mark.parametrize("content", ["[1, 2, 3]", '"字符串"', "42", "null"])
def should_backup_and_reset_when_payload_not_object(tmp_path, content):
    path = tmp_path / "cfg.json"
    path.write_text(content, encoding="utf-8")

    assert load_json_or_backup_reset(path) is None
    assert list(tmp_path.glob("cfg.json.bak-*"))
    assert not path.exists()


def should_reset_when_file_bytes_not_utf8(tmp_path):
    path = tmp_path / "bin.json"
    path.write_bytes(b"\xff\xfe\x00\x01bad")

    assert load_json_or_backup_reset(path) is None


def should_degrade_quietly_when_backup_move_fails(tmp_path, monkeypatch):
    """备份也失败（文件被占用等）：只降级返回默认值，绝不让启动崩溃。"""
    path = tmp_path / "locked.json"
    path.write_text("{{{", encoding="utf-8")
    monkeypatch.setattr(shutil, "move", _raise_locked)

    assert load_json_or_backup_reset(path) is None
    assert load_json_or_backup_reset(path) is None    # 再次读取仍稳定降级


def _raise_locked(*_args, **_kwargs):
    raise OSError("file locked by another process")
