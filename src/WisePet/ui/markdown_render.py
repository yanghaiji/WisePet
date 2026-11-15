"""完整 markdown → 带内联样式的富文本 HTML（气泡 / 历史对话窗共用）。

基于 mistune（纯 Python、零传递依赖、毫秒级渲染，流式每帧重渲也不卡）：
- escape=True：AI 输出的原始 HTML 一律转义，注入无门；
- 插件：strikethrough（~~删除线~~）/ table（表格）/ url（自动识别链接）；
- 内联样式直接写进标签：QLabel 与 QTextBrowser 都吃这套，气泡与历史窗
  共用同一渲染结果，视觉一致。

可选依赖（规约 §2.9，同 jieba 的处理）：mistune 缺失时降级为
「转义 + 换行」的纯文本富文本——不崩、安全，只是没有样式。
"""
from __future__ import annotations

try:
    import mistune
except ImportError:                       # 环境没装：降级纯文本，不崩
    mistune = None

_MONO = "font-family:Consolas,'Courier New',monospace;"


def _fallback_html(text: str) -> str:
    """mistune 缺失时的降级渲染：转义 + 换行（安全但没有样式）。"""
    import html as _html
    return _html.escape(str(text or ""), quote=False).replace("\n", "<br>").strip()


if mistune is None:                       # 可选依赖缺失：降级，不定义渲染器
    _md = None
else:

    class _ThemeRenderer(mistune.HTMLRenderer):
        """贴纸风主题：按 app 配色给每个块级元素写内联样式。"""

        def paragraph(self, text: str) -> str:
            return f'<p style="margin:3px 0;">{text}</p>'

        def heading(self, text: str, level: int, **attrs) -> str:
            size = {1: 15, 2: 14, 3: 13, 4: 12, 5: 12, 6: 12}.get(level, 12)
            return (f'<h{level} style="color:#5A3E28;font-size:{size}px;'
                    f'font-weight:600;margin:6px 0 2px 0;">{text}</h{level}>')

        def codespan(self, text: str) -> str:
            return (f'<code style="background:#F7EEDC;color:#8A5A2B;{_MONO}'
                    f'border-radius:3px;padding:0 3px;">{text}</code>')

        def block_code(self, code: str, info: str | None = None) -> str:
            return (f'<pre style="background:#F7EEDC;border:1px solid #E7D3B2;'
                    f'border-radius:6px;padding:6px;color:#4A3A2A;{_MONO}'
                    f'white-space:pre-wrap;margin:4px 0;">{code}</pre>')

        def block_quote(self, text: str) -> str:
            return (f'<blockquote style="border-left:3px solid #E8A45C;'
                    f'color:#7A5C3D;margin:4px 0;padding:2px 0 2px 8px;">{text}</blockquote>')

        def list(self, text: str, ordered: bool, **attrs) -> str:
            return f'<{"ol" if ordered else "ul"} style="margin:3px 0 3px 18px;">{text}</{"ol" if ordered else "ul"}>'

        def table(self, text: str) -> str:
            return (f'<table cellpadding="4" cellspacing="0" style="border:1px solid #E2CFAF;'
                    f'margin:4px 0;">{text}</table>')

        def table_head(self, text: str) -> str:
            return f'<thead style="background:#F7EEDC;color:#5A3E28;">{text}</thead>'

        def table_body(self, text: str) -> str:
            return f'<tbody>{text}</tbody>'

        def table_row(self, text: str) -> str:
            return f'<tr style="border-bottom:1px solid #EFE0C6;">{text}</tr>'

        def link(self, text: str, url: str, title: str | None = None) -> str:
            return f'<a href="{mistune.escape_url(url)}" style="color:#C2712D;">{text}</a>'

        def thematic_break(self) -> str:
            return '<hr style="border:0;border-top:1px solid #EFE0C6;margin:6px 0;">'

        def strikethrough(self, text: str) -> str:
            return f"<s>{text}</s>"

        def image(self, text: str, url: str, title: str | None = None) -> str:
            # 气泡/历史窗不加载远程图片（隐私口径）：降级为可点链接
            label = text or url
            return self.link(label, url, title)

    _md = mistune.create_markdown(
        renderer=_ThemeRenderer(), escape=True,
        plugins=("strikethrough", "table", "url"))


def render_markdown(text: str) -> str:
    """markdown 源文本 → 主题化富文本 HTML；空输入返回空串。"""
    source = str(text or "")
    if _md is None:
        return _fallback_html(source)
    return _md(source).strip()
