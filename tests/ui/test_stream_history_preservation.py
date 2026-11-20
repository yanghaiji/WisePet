"""回归：历史窗含图片时，流式重写多轮 + 点保存，都不能丢历史消息。

真机反馈：历史有图、点「保存图片」取消后 28 条全没。根因是旧方案用
QTextCursor 块号定位流气泡，图片/表格的富文本块数漂移导致误删前文。
"""
from __future__ import annotations

from PySide6.QtGui import QColor, QImage

from WisePet.domain.chat_record import ChatRecord, day_key
from WisePet.ui.chat_window import ChatWindow


def _record(content, images=()):
    return ChatRecord(role="assistant", content=content, source="ai",
                      ts=1_700_000_000.0, session_id=day_key(), images=images)


def test_stream_rewrite_preserves_image_history(qtbot, fake_screen, tmp_path):
    w = ChatWindow(fake_screen, records_provider=lambda _d: [])
    qtbot.addWidget(w)
    img = tmp_path / "cat.png"
    canvas = QImage(4, 4, QImage.Format.Format_ARGB32)
    canvas.fill(QColor("#ff0000"))
    canvas.save(str(img))

    w.load_history([_record("前面有图的消息", (str(img),)),
                    _record("第二条历史")])

    w.begin_stream_reply()
    for piece in ("新", "回", "复"):
        w.append_stream_piece(piece)
        # 每轮重写后，前面的历史必须还在
        html = w._history.toHtml()
        assert "前面有图的消息" in html, f"第 {piece} 轮重写丢了第一条历史"
        assert "第二条历史" in html, f"第 {piece} 轮重写丢了第二条历史"

    w.end_stream_reply("新回复")
    final = w._history.toHtml()
    assert "前面有图的消息" in final and "第二条历史" in final and "新回复" in final
    assert w._count == 3
