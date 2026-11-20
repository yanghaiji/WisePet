"""生成图片另存：复制到用户选的路径，原件保留供历史回看（无 Qt 依赖部分）。"""
from __future__ import annotations

import shutil
from pathlib import Path

from WisePet.ui.image_io import _copy_image


def test_copy_image_to_target_keeps_source(tmp_path):
    src = tmp_path / "generated" / "cat.png"
    src.parent.mkdir()
    src.write_bytes(b"PNGDATA")
    target = tmp_path / "saved" / "mycat.png"
    target.parent.mkdir()

    assert _copy_image(str(src), str(target)) == str(target)
    assert target.read_bytes() == b"PNGDATA"
    assert src.read_bytes() == b"PNGDATA"       # 原件还在，历史回看不失效


def test_copy_image_missing_source_returns_empty(tmp_path):
    assert _copy_image(str(tmp_path / "missing.png"), str(tmp_path / "x.png")) == ""
