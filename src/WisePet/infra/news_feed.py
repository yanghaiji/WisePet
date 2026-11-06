"""RSS 新闻拉取与解析：纯 httpx + 标准库 XML，不引入 feedparser 依赖。

只取标题做播报/摘要素材；单源失败不影响其它源（个人桌面场景，源随时可能挂）。
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_S = 8.0
_USER_AGENT = "WisePet/1.0 (+https://github.com/wisepet)"


def parse_feed(xml_text: str) -> list[str]:
    """兼容 RSS 2.0（<item><title>）与 Atom（<entry><title>），忽略命名空间。"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        logger.debug("feed xml parse failed")
        return []
    titles: list[str] = []
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] not in ("item", "entry"):
            continue
        for child in node:
            if child.tag.rsplit("}", 1)[-1] == "title" and (child.text or "").strip():
                title = " ".join(child.text.split())
                titles.append(title)
                break
    return titles


def fetch_headlines(sources, *, timeout_s: float = _DEFAULT_TIMEOUT_S,
                    per_source: int = 8) -> list[str]:
    """依次拉各源取标题；去重保序，单源异常记 debug 后继续。"""
    headlines: list[str] = []
    seen: set[str] = set()
    with httpx.Client(timeout=timeout_s, follow_redirects=True,
                      headers={"User-Agent": _USER_AGENT}) as client:
        for source in sources:
            url = str(source or "").strip()
            if not url:
                continue
            try:
                resp = client.get(url)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                logger.debug("news source failed: %s (%s)", url, exc)
                continue
            for title in parse_feed(resp.text)[:per_source]:
                if title not in seen:
                    seen.add(title)
                    headlines.append(title)
    return headlines
