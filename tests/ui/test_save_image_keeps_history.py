"""回归：点保存图片（含取消）不能让历史区内容消失。

真机反馈：历史窗点「保存图片」取消后，28 条对话全没了，只剩空区。
"""
from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QColor, QImage

from WisePet.domain.chat_record import ChatRecord, day_key
from WisePet.ui import image_io
from WisePet.ui.chat_window import ChatWindow


def _record(content, images=()):
    return ChatRecord(role="assistant", content=content, source="ai",
                      ts=1_700_000_000.0, session_id=day_key(), images=images)


def test_save_image_cancel_keeps_history(qtbot, fake_screen, tmp_path, monkeypatch):
    w = ChatWindow(fake_screen, records_provider=lambda _d: [])
    qtbot.addWidget(w)
    img = tmp_path / "cat.png"
    canvas = QImage(4, 4, QImage.Format.Format_ARGB32)
    canvas.fill(QColor("#ff0000"))
    canvas.save(str(img))

    w.load_history([_record("第一条", (str(img),)), _record("第二条")])
    before = w._history.toHtml()
    assert "第一条" in before and "第二条" in before

    # 用户点保存 → 系统对话框返回取消（空串）
    monkeypatch.setattr(image_io, "save_image_copy", lambda *a, **k: "")
    key = None
    for k, p in w._image_paths.items():
        if p == str(img):
            key = k
    assert key
    w._on_anchor_clicked(QUrl(f"wisepet-save:{key}"))

    after = w._history.toHtml()
    assert "第一条" in after and "第二条" in after, "保存取消后历史被清空了"
    assert w._count == 2
