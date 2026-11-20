"""生成图片的本地另存：气泡与历史窗共用的「保存图片」入口。

图片由 agent 生成、只落在本机数据目录；用户想留一份到别处时走系统
QFileDialog 另存，复制字节而不是移动原文件（历史回看仍指向原件）。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QWidget


def _copy_image(source: str, target: str) -> str:
    """把源图片复制到目标路径（不覆盖原件）；成功返回目标，失败返回空串。"""
    src = Path(str(source or ""))
    if not src.is_file() or not str(target or "").strip():
        return ""
    try:
        if Path(target).resolve() == src.resolve():
            return str(target)                 # 存回原处：无需复制
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
    except OSError:
        return ""
    return str(target)


def save_image_copy(source: str, parent: QWidget | None = None,
                    caption: str = "保存图片") -> str:
    """弹系统另存对话框，把 source 复制到用户选的路径；返回目标路径，取消返回空串。"""
    src = Path(str(source or ""))
    if not src.is_file():
        return ""
    suffix = src.suffix or ".png"
    target, _ = QFileDialog.getSaveFileName(
        parent, caption, str(Path.home() / f"wisepet-{src.stem}{suffix}"),
        f"图片 (*{suffix} *.png *.jpg);;所有文件 (*)")
    if not target:
        return ""
    return _copy_image(str(src), str(target))
