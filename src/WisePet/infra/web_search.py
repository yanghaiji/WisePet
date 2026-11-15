"""联网搜索网关：agent 的 web_search 工具的后端实现。

四家供应商，按配置选择：
- bing：必应 cn.bing.com，免 Key、国内直连可用（默认方案）；
- duckduckgo：免 Key，抓取 DDG Lite 结果页（国内网络通常不通）；
- tavily：https://tavily.com 的 API（海外效果好，有免费额度）；
- bocha：博查 api.bochaai.com（国内效果好，付费 Key）。

SSL：默认用「certifi + Windows 系统证书」合并的信任链——本机代理 /
安全软件做 HTTPS 拦截时签发的是系统信任的自签证书，纯 certifi 会
CERTIFICATE_VERIFY_FAILED（真机日志 2026-11-12），合并后两端都兼容。

统一输出 WebResult 列表；网络/解析失败返回空列表并记日志，
由工具层给模型一句人话兜底——搜索失败不该让整轮对话崩掉。
"""
from __future__ import annotations

import logging
import re
import ssl
from dataclasses import dataclass
from html import unescape as _html_unescape
from urllib.parse import unquote

import httpx

try:  # certifi 是 httpx 的传递依赖，拿不到就用系统默认
    import certifi
except ImportError:  # pragma: no cover
    certifi = None

logger = logging.getLogger(__name__)

#: 单次搜索超时：工具循环里同步等待，太长用户会觉得"卡住"
SEARCH_TIMEOUT_S = 12.0
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_PROVIDERS = ("bing", "duckduckgo", "tavily", "bocha")
DEFAULT_PROVIDER = "bing"

#: 合并信任链（模块级只建一次）：certifi 根 + Windows CA/ROOT 系统证书。
#: 非 Windows 或枚举失败时安全回落纯 certifi。
def _merged_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=certifi.where() if certifi else None)
    if hasattr(ssl, "enum_certificates"):
        for store in ("CA", "ROOT"):
            for cert, enc, _type in ssl.enum_certificates(store):
                try:
                    data = cert.decode() if enc == 1 else cert
                    ctx.load_verify_locations(cadata=data)
                except Exception:      # noqa: BLE001 - 单张证书坏了不影响整体
                    continue
    return ctx


class _HttpClient:
    """进程级共享的 httpx 客户端：复用连接池与信任链。"""
    client: httpx.Client | None = None

    @classmethod
    def get(cls) -> httpx.Client:
        if cls.client is None:
            cls.client = httpx.Client(
                verify=_merged_ssl_context(), follow_redirects=True,
                timeout=SEARCH_TIMEOUT_S, headers={"User-Agent": _UA})
        return cls.client


@dataclass(frozen=True)
class WebResult:
    title: str
    url: str
    snippet: str


def _clean_text(raw: str) -> str:
    """去标签 + 还原 HTML 实体（&ensp; &#0183; 之类），压掉多余空白。"""
    text = _html_unescape(re.sub(r"<[^>]+>", "", raw))
    return re.sub(r"\s+", " ", text).strip()


class WebSearchGateway:
    """按配置把 query 交给所选供应商，返回去重后的结果列表。"""

    def __init__(self, provider_provider=None, key_provider=None,
                 max_results: int = 5):
        # provider/key 用回调惰性取：设置面板改完立即生效，不用重建对象
        self._provider_provider = provider_provider
        self._key_provider = key_provider
        self._max_results = max(1, int(max_results))

    @property
    def provider(self) -> str:
        raw = (self._provider_provider
               or (lambda: DEFAULT_PROVIDER))()
        # 传入的可能是配置对象（带 .provider 字段）或纯字符串，两者都兼容
        name = str(getattr(raw, "provider", raw) or "").strip().lower()
        return name if name in _PROVIDERS else DEFAULT_PROVIDER

    def enabled(self) -> bool:
        cfg = self._provider_provider
        if cfg is None:
            return True
        try:
            return bool(getattr(cfg(), "enabled", True))
        except Exception:  # noqa: BLE001 - 配置回调异常不影响主流程
            return True

    def search(self, query: str) -> list[WebResult]:
        provider = self.provider
        key = str((self._key_provider or (lambda: ""))() or "").strip()
        try:
            if provider == "bing":
                return self._bing(query)
            if provider == "tavily":
                return self._tavily(query, key)
            if provider == "bocha":
                return self._bocha(query, key)
            return self._duckduckgo(query)
        except Exception as exc:  # noqa: BLE001 - 搜索失败降级为空结果
            logger.info("web search via %s failed: %s", provider, exc)
            return []

    # ---------- 供应商 ----------

    def _bing(self, query: str) -> list[WebResult]:
        """免 Key 默认方案：cn.bing.com 国内直连；中国区无 cookie 时走国际版。"""
        resp = _HttpClient.get().get(
            "https://cn.bing.com/search",
            params={"q": query, "count": self._max_results, "mkt": "zh-CN",
                    "setlang": "zh-hans"},
            headers={"Accept-Language": "zh-CN,zh;q=0.9"})
        resp.raise_for_status()
        return self._parse_bing(resp.text)

    @staticmethod
    def _parse_bing(html: str) -> list[WebResult]:
        """解析必应结果：<li class="b_algo"> 内的 <h2><a> 与摘要 <p>。"""
        out: list[WebResult] = []
        seen: set[str] = set()
        for chunk in html.split('class="b_algo"')[1:]:
            link = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                             chunk, re.S | re.I)
            if not link:
                continue
            url = link.group(1)
            title = _clean_text(link.group(2))
            snippet_m = re.search(r'<p[^>]*class="[^"]*b_lineclamp[^"]*"[^>]*>(.*?)</p>',
                                  chunk, re.S | re.I) \
                or re.search(r'<p[^>]*>(.*?)</p>', chunk, re.S | re.I)
            snippet = _clean_text(snippet_m.group(1)) if snippet_m else ""
            if not title or url in seen or url.startswith("javascript:"):
                continue
            seen.add(url)
            out.append(WebResult(title, url, snippet[:300]))
            if len(out) >= 8:
                break
        return out

    def _tavily(self, query: str, key: str) -> list[WebResult]:
        if not key:
            raise ValueError("Tavily 需要在设置里填 API Key")
        resp = _HttpClient.get().post(
            "https://api.tavily.com/search",
            json={"api_key": key, "query": query,
                  "max_results": self._max_results,
                  "search_depth": "basic"})
        resp.raise_for_status()
        out: list[WebResult] = []
        for item in (resp.json().get("results") or [])[:self._max_results]:
            out.append(WebResult(str(item.get("title") or ""),
                                 str(item.get("url") or ""),
                                 str(item.get("content") or "")[:300]))
        return out

    def _bocha(self, query: str, key: str) -> list[WebResult]:
        if not key:
            raise ValueError("博查搜索需要在设置里填 API Key")
        resp = _HttpClient.get().post(
            "https://api.bochaai.com/v1/web-search",
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            json={"query": query, "freshness": "noLimit",
                  "summary": True, "count": self._max_results})
        resp.raise_for_status()
        pages = ((resp.json().get("data") or {}).get("webPages") or {})
        out: list[WebResult] = []
        for item in (pages.get("value") or [])[:self._max_results]:
            out.append(WebResult(str(item.get("name") or ""),
                                 str(item.get("url") or ""),
                                 str(item.get("summary")
                                     or item.get("snippet") or "")[:300]))
        return out

    def _duckduckgo(self, query: str) -> list[WebResult]:
        """免 Key 备选：DDG Lite 结果页（POST，避开 GET 重定向拦截）。"""
        resp = _HttpClient.get().post(
            "https://lite.duckduckgo.com/lite/",
            data={"q": query},
            headers={"Referer": "https://lite.duckduckgo.com/"})
        resp.raise_for_status()
        return self._parse_ddg_lite(resp.text)

    @staticmethod
    def _parse_ddg_lite(html: str) -> list[WebResult]:
        """极简解析 DDG Lite 表格结构：<a class=result-link> 与相邻摘要行。"""
        rows = re.findall(
            r'<a[^>]+class="result-link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
            r'.*?<td[^>]*class="result-snippet"[^>]*>(.*?)</td>',
            html, re.S | re.I)
        out: list[WebResult] = []
        seen: set[str] = set()
        for url, title, snippet in rows:
            title = re.sub(r"<[^>]+>", "", title).strip()
            snippet = re.sub(r"<[^>]+>", "", snippet).strip()
            # DDG 的跳转链接解开真实 url
            m = re.search(r"uddg=([^&]+)", url)
            if m:
                url = unquote(m.group(1))
            if not title or url in seen:
                continue
            seen.add(url)
            out.append(WebResult(title, url, snippet[:300]))
            if len(out) >= 8:
                break
        return out
