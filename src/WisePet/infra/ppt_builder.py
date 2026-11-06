"""python-pptx 封装：模型生成的标题+要点大纲 → 本地 .pptx 文件。

只做模板化排版（封面 + 标题/要点页），追求「能交付」而不是「能惊艳」；
样式美化交给用户在 PowerPoint/WPS 里挑模板。
"""
from __future__ import annotations

import logging
from pathlib import Path

from pptx import Presentation

logger = logging.getLogger(__name__)


def build_ppt(path: str, title: str, slides: list[dict]) -> Path:
    """生成 .pptx；path 无扩展名时补 .pptx，父目录不存在时自动创建。"""
    target = Path(path).expanduser()
    if target.suffix.lower() != ".pptx":
        target = target.with_suffix(".pptx")
    target.parent.mkdir(parents=True, exist_ok=True)

    prs = Presentation()
    cover = prs.slides.add_slide(prs.slide_layouts[0])
    cover.shapes.title.text = title
    if len(cover.placeholders) > 1:
        cover.placeholders[1].text = "由 WisePet 桌宠生成"

    for slide_spec in slides:
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = str(slide_spec.get("title") or "")
        body = slide.placeholders[1].text_frame
        for index, bullet in enumerate(slide_spec.get("bullets") or ()):
            paragraph = body.paragraphs[0] if index == 0 else body.add_paragraph()
            paragraph.text = f"• {bullet}"

    prs.save(str(target))
    logger.info("ppt built: %s (%d slides)", target, len(slides) + 1)
    return target
