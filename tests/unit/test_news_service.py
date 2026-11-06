"""NewsService / RSS 解析 / PPT 构建测试（工具执行层后台任务）。"""
import time

from WisePet.app.news_service import NewsService
from WisePet.domain.config_model import AppConfig, NewsSettings
from WisePet.domain.events import SpeakRequestedEvent
from WisePet.infra.news_feed import parse_feed
from WisePet.infra.ppt_builder import build_ppt
from fakes import FakeClock, make_collecting_bus

RSS_SAMPLE = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<title>科技源</title>
<item><title>第一条新闻</title><link>https://a/1</link></item>
<item><title>  第二条   新闻 </title></item>
</channel></rss>"""

ATOM_SAMPLE = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Atom 头条</title></entry>
<entry><title>Atom 第二条</title></entry>
</feed>"""


def make_svc(fetcher, summarizer=None, *, news: NewsSettings | None = None):
    cfg = AppConfig()
    if news is not None:
        cfg.news = news
    clock = FakeClock()
    bus, collected = make_collecting_bus()
    service = NewsService(clock, bus, lambda: cfg, fetcher,
                          summarizer=summarizer)
    return service, clock, collected, cfg


class TestParseFeed:
    def should_parse_rss_items(self):
        assert parse_feed(RSS_SAMPLE) == ["第一条新闻", "第二条 新闻"]

    def should_parse_atom_entries(self):
        assert parse_feed(ATOM_SAMPLE) == ["Atom 头条", "Atom 第二条"]

    def should_return_empty_for_garbage(self):
        assert parse_feed("not xml at all <") == []


class TestNewsService:
    def should_publish_plain_headlines_when_no_ai(self):
        fetcher = lambda sources: ["新闻A", "新闻B"]  # noqa: E731
        service, clock, collected, _cfg = make_svc(fetcher)
        service.start()
        clock.advance(3601)
        service.tick()
        # tick 起后台线程：轮询等事件落地（daemon 线程很快结束）
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not collected:
            time.sleep(0.01)
        speaks = [e for e in collected if isinstance(e, SpeakRequestedEvent)]
        assert len(speaks) == 1
        assert "新闻A" in speaks[0].text and speaks[0].source == "news"

    def should_use_ai_summary_when_available(self):
        fetcher = lambda sources: ["新闻A"]  # noqa: E731
        service, clock, collected, _cfg = make_svc(
            fetcher, summarizer=lambda hs: "AI摘要：新闻A")
        service.start()
        clock.advance(3601)
        service.tick()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not collected:
            time.sleep(0.01)
        speaks = [e for e in collected if isinstance(e, SpeakRequestedEvent)]
        assert speaks and speaks[0].text == "AI摘要：新闻A"

    def should_skip_when_disabled(self):
        fetcher = lambda sources: (_ for _ in ()).throw(AssertionError("不应拉取"))  # noqa: E731
        news = NewsSettings(enabled=False)
        service, clock, collected, _cfg = make_svc(fetcher, news=news)
        service.start()
        clock.advance(3601)
        service.tick()
        assert collected == []

    def should_not_overlap_when_refresh_inflight(self):
        started = []
        fetcher = lambda sources: (started.append(1) or ["x"])  # noqa: E731
        service, clock, _collected, _cfg = make_svc(fetcher)
        service.start()
        clock.advance(3601)
        service.tick()
        service.tick()          # inflight 中的第二轮不应拉起
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not started:
            time.sleep(0.01)
        assert len(started) == 1


class TestPptBuilder:
    def should_build_ppt_file(self, tmp_path):
        target = tmp_path / "demo.pptx"
        saved = build_ppt(str(target), "季度汇报",
                          [{"title": "背景", "bullets": ["要点一", "要点二"]},
                           {"title": "计划", "bullets": []}])
        assert saved == target
        assert target.stat().st_size > 0

    def should_add_pptx_suffix_and_create_dirs(self, tmp_path):
        saved = build_ppt(str(tmp_path / "sub" / "demo"), "标题",
                          [{"title": "一", "bullets": ["b"]}])
        assert saved.name == "demo.pptx" and saved.is_file()
