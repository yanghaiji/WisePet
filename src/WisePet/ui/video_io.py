"""生成视频的本地打开与另存：气泡与历史窗共用。

视频文件只落本机数据目录；点「播放」用系统默认播放器打开（Qt 富文本不支持
内嵌 video），点「另存」走 QFileDialog 复制到用户指定位置。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QFileDialog, QWidget


def play_video(path: str) -> bool:
    """用系统默认播放器打开视频；成功返回 True。"""
    p = Path(str(path or ""))
    if not p.is_file():
        return False
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(p.resolve())))


def save_video_copy(source: str, parent: QWidget | None = None,
                    caption: str = "保存视频") -> str:
    """弹系统另存对话框，把 source 复制到目标；取消返回空串。"""
    src = Path(str(source or ""))
    if not src.is_file():
        return ""
    suffix = src.suffix or ".mp4"
    target, _ = QFileDialog.getSaveFileName(
        parent, caption, str(Path.home() / f"wisepet-video-{src.stem}{suffix}"),
        f"视频 (*{suffix} *.mp4 *.webm);;所有文件 (*)")
    if not target:
        return ""
    try:
        if Path(target).resolve() != src.resolve():
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)
    except OSError:
        return ""
    return str(target)
