"""可选天气推送回归：纯逻辑 / 网关离线 / 服务去重 / 配置往返 / 事件集成。"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from WisePet.app.weather_service import WeatherService
from WisePet.domain.config_model import AppConfig
from WisePet.domain.weather import care_line, describe_code, is_bad_weather
from WisePet.infra.weather_api import WeatherGateway, WeatherSnapshot


# ---------- 纯逻辑 ----------

def test_describe_code_and_bad_weather():
    assert describe_code(0) == "晴"
    assert describe_code(63) == "下雨"
    assert describe_code(75) == "下雪"
    assert describe_code(999) == "天气多变"
    assert describe_code("bad") == "天气多变"
    assert is_bad_weather(63) is True
    assert is_bad_weather(0) is False
    assert is_bad_weather(None) is False


def test_care_line_varies_by_weather_and_temp():
    rain = care_line(code=63, temp=20, city="长春", humidity="80%")
    assert "伞" in rain and "20℃" in rain and "湿度 80%" in rain
    snow = care_line(code=75, temp=-3, city="长春", low="低温 -8℃", high="高温 1℃")
    assert "穿暖" in snow and "-3℃" in snow and "今日 -8℃/1℃" in snow
    assert "冷" in care_line(code=0, temp=2)
    assert "喝水" in care_line(code=0, temp=36)
    assert "晴" in care_line(code=0, temp=22)


def test_broadcast_line_has_temperature():
    """主动播报必须带温度（用户真机反馈：只说「挺冷的」看不到温度）。"""
    from WisePet.app.weather_service import WeatherService
    cfg = AppConfig.from_dict({"weather": {"enabled": True}})
    gw = WeatherGateway(
        location_provider=lambda: SimpleNamespace(city="长春"),
        http_get=lambda url, params=None, timeout=None: SimpleNamespace(json=lambda: {
            "cityInfo": {"city": "长春市"},
            "data": {"wendu": "-6.2", "shidu": "51%",
                     "forecast": [{"type": "多云", "low": "低温 -8℃",
                                   "high": "高温 0℃"}]}}))
    svc = WeatherService(gw, lambda: cfg, fetcher=lambda fn: fn())
    svc.request_fetch(force=True)
    line = svc.due_line(1_700_000_000.0, day_key_fn=lambda _: "2026-11-18")
    assert "-6℃" in line and "湿度 51%" in line and "多云" in line
    assert "℃" in line                      # 一定有温度数字


# ---------- 网关离线（注入 http_get / location_provider） ----------

def test_gateway_prefers_domestic_itboy_source():
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        if "itboy" in url:
            return SimpleNamespace(json=lambda: {
                "cityInfo": {"city": "沈阳市"},
                "data": {"wendu": "-2.3",
                         "forecast": [{"type": "多云"}]}})
        return SimpleNamespace(json=lambda: {
            "results": [{"name": "X", "latitude": 1, "longitude": 2}],
            "current": {"weather_code": 0, "temperature_2m": 99.0}})

    gw = WeatherGateway(
        location_provider=lambda: SimpleNamespace(city="沈阳"),
        http_get=fake_get)
    snap = gw.fetch()
    # 国内源命中：用它的温度，不碰 open-meteo
    assert snap.city == "沈阳市" and snap.temp == -2.3 and snap.code == 2
    assert any("itboy" in u for u in calls)
    assert not any("geocoding" in u or "forecast" in u for u in calls)


def test_gateway_falls_back_to_open_meteo_when_city_not_in_table():
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        if "itboy" in url:
            raise RuntimeError("domestic down")
        if "geocoding" in url:
            return SimpleNamespace(json=lambda: {
                "results": [{"name": "Lima", "latitude": -12.0, "longitude": -77.0}]})
        return SimpleNamespace(json=lambda: {
            "current": {"weather_code": 0, "temperature_2m": 20.0}})

    gw = WeatherGateway(
        location_provider=lambda: SimpleNamespace(city="Lima"),
        http_get=fake_get)
    snap = gw.fetch()
    assert snap.code == 0 and snap.temp == 20.0
    assert any("geocoding" in u for u in calls) and any("forecast" in u for u in calls)


def test_gateway_skips_on_no_location_or_empty_city():
    def no_loc():
        return None
    assert WeatherGateway(location_provider=no_loc).fetch() is None
    assert WeatherGateway(
        location_provider=lambda: SimpleNamespace(city="")).fetch() is None


def test_gateway_skips_on_exception():
    def boom(url, params=None, timeout=None):
        raise RuntimeError("offline")
    gw = WeatherGateway(
        location_provider=lambda: SimpleNamespace(city="", lat=1, lon=2),
        http_get=boom)
    assert gw.fetch() is None


# ---------- WeatherService ----------

def _svc(tmp_path=None, enabled=True):
    cfg = replace(AppConfig(), weather=replace(AppConfig().weather, enabled=enabled))
    gw = WeatherGateway(
        location_provider=lambda: SimpleNamespace(city="长春", lat=43, lon=125),
        http_get=lambda url, params=None, timeout=None: SimpleNamespace(
            json=lambda: {"results": [{"name": "长春", "latitude": 43, "longitude": 125}]}
            if "geocoding" in url else
            {"current": {"weather_code": 0, "temperature_2m": 22.0}}))
    svc = WeatherService(gw, lambda: cfg, fetcher=lambda fn: fn())  # 同步执行便于测
    return svc, cfg


def test_service_reports_every_interval_minutes():
    cfg = AppConfig.from_dict({"weather": {"enabled": True, "interval_min": 60}})
    from WisePet.infra.weather_api import WeatherGateway as WG
    fake_gw = WG(location_provider=lambda: SimpleNamespace(city="长春"),
                 http_get=lambda url, params=None, timeout=None: SimpleNamespace(
                     json=lambda: {"cityInfo": {"city": "长春"},
                                   "data": {"wendu": "20", "forecast": [{"type": "晴"}]}}))
    svc = WeatherService(fake_gw, lambda: cfg, fetcher=lambda fn: fn())
    svc.request_fetch(force=True)
    day = lambda ts: "2026-11-17"
    base = 1_700_000_000.0
    # 首次立即报，随后 60 分钟内不报，超过间隔再报
    assert "晴" in svc.due_line(base, day_key_fn=day)
    assert svc.due_line(base + 30 * 60, day_key_fn=day) == ""
    assert "晴" in svc.due_line(base + 61 * 60, day_key_fn=day)
    # 新的一天重置：立即又能报一次
    assert "晴" in svc.due_line(base + 25 * 3600,
                                day_key_fn=lambda _: "2026-11-18")


def test_service_disabled_never_reports():
    svc, _cfg = _svc(enabled=False)
    svc.request_fetch(force=True)
    assert svc.snapshot() is None                          # 关闭时根本不抓
    assert svc.due_line(0.0, day_key_fn=lambda _: "x") == ""


# ---------- 配置往返 ----------

def test_weather_settings_roundtrip_and_clamp():
    cfg = AppConfig.from_dict({"weather": {"enabled": True, "interval_min": 120,
                                            "bad_weather_alert": False}})
    assert cfg.weather.enabled is True and cfg.weather.interval_min == 120
    assert cfg.weather.bad_weather_alert is False
    assert AppConfig.from_dict(cfg.to_dict()).weather.interval_min == 120
    bad = AppConfig.from_dict({"weather": {"interval_min": 1}})   # 下限 30
    assert bad.weather.interval_min >= 30
    # 旧固定时刻配置升级为「每 24 小时一次」
    legacy = AppConfig.from_dict({"weather": {"report_hour": 7, "report_minute": 30}})
    assert legacy.weather.interval_min == 1440
    assert AppConfig().weather.enabled is False            # 默认关闭
    assert AppConfig().weather.interval_min == 180         # 默认 3 小时


# ---------- 聊天天气工具 ----------

def test_format_report_has_time_temp_humidity():
    from WisePet.domain.weather import format_report
    report = format_report(city="长春市", code=63, temp=-6.2,
                           humidity="45%", wind="西北风 1级",
                           high="高温 4℃", low="低温 -8℃",
                           notice="阴晴之间，谨防紫外线侵扰", observed_at="12:57")
    assert "###" in report and "长春市" in report          # markdown 标题
    assert "- **天气**" in report and "- **气温**" in report  # 加粗列表
    assert "查询时间" in report and "观测时间" in report
    assert "-6℃" in report and "湿度**：45%" in report
    assert "西北风 1级" in report and "低温 -8℃" in report
    assert "> 小提示：" in report


def test_get_weather_tool_returns_formatted_report():
    from WisePet.app.agent_tools import AgentToolbox

    fake_gw = SimpleNamespace(fetch=lambda: WeatherSnapshot(
        city="长春市", code=63, temp=18.0, humidity="50%",
        wind="东风 2级", high="高温 25℃", low="低温 10℃",
        observed_at="16:30"))
    tb = AgentToolbox(weather=fake_gw)
    text, _ = tb.execute("get_weather", {})
    assert "###" in text and "- **气温**" in text and "查询时间" in text
    assert "下雨" in text and "18℃" in text and "湿度**：50%" in text
    assert "高温 25℃" in text and "原样输出" in text


def test_ensure_card_format_recovers_lost_markdown():
    from WisePet.domain.weather import ensure_card_format
    card = "### 长春 · 天气\n\n- **气温**：-6℃\n- **湿度**：45%\n\n——请原样输出"
    # 模型把卡片揉成一段话 → 用卡片兜底
    flat = "今天长春天气不错，气温大概-6度，湿度45%哦。"
    out = ensure_card_format(flat, card)
    assert out.startswith("### 长春") and "- **气温**" in out
    assert "——" not in out                      # 指令部分不带给用户
    assert "今天长春天气不错" in out            # 模型口语化的话保留

    # 模型已照抄格式 → 原样放行
    good = "### 长春 · 天气\n\n- **气温**：-6℃"
    assert ensure_card_format(good, card) == good


def test_get_weather_tool_skips_when_unavailable():
    from WisePet.app.agent_tools import AgentToolbox

    tb = AgentToolbox(weather=None)
    text, _ = tb.execute("get_weather", {})
    assert "查不到" in text and "不要编造" in text

    tb2 = AgentToolbox(weather=SimpleNamespace(fetch=lambda: None))
    text2, _ = tb2.execute("get_weather", {})
    assert "查不到" in text2


def test_agent_loop_recovers_weather_card_format():
    from fakes import FakeGateway
    from WisePet.app.agent_loop import AgentRunner
    from WisePet.app.agent_tools import AgentToolbox
    from WisePet.domain.ports import AIConfig

    card = ("### 长春 · 天气\n\n- **气温**：-6℃\n- **湿度**：45%\n\n"
            "——请把这段 markdown 原样输出")
    gw = FakeGateway(tools_results=[
        ("让我查一下", [{"id": "c1", "name": "get_weather", "arguments": {}}]),
        ("今天长春气温大概-6度湿度45%哦，多穿点。", []),   # 模型揉成一段
    ])
    fake_weather = SimpleNamespace(fetch=lambda: WeatherSnapshot(
        city="长春", code=45, temp=-6.0, humidity="45%"))
    tb = AgentToolbox(weather=fake_weather)
    runner = AgentRunner(gw, lambda: AIConfig(model="m"),
                         lambda: "你是桌面宠物。", tb)
    out = runner.run("今天天气怎么样", [])
    assert "### 长春" in out.final_text and "- **气温**" in out.final_text
    assert "get_weather" in out.tools_used


# ---------- 事件触发集成 ----------

def test_event_trigger_fires_weather_candidate_once(tmp_path):
    from fakes import FakeClock, make_collecting_bus
    from WisePet.app.event_triggers import EventTriggerService
    from WisePet.domain.events import SpeakRequestedEvent
    from WisePet.infra.event_store import EventStore

    base = 1_700_000_000.0
    clock = FakeClock()
    bus, collected = make_collecting_bus()
    cfg = AppConfig()
    svc = EventTriggerService(
        clock, bus, lambda: cfg, EventStore(tmp_path),
        idle_provider=lambda: 10.0, focus_count_provider=lambda: 0,
        weather_line_provider=lambda now, day_key_fn: "外面下雨呢，带伞呀～")
    svc.tick(base)
    titles = [e.title for e in collected if isinstance(e, SpeakRequestedEvent)]
    assert "今日天气" in titles
    assert all(e.source == "weather" for e in collected if isinstance(e, SpeakRequestedEvent))
    # 间隔/当日去重由 WeatherService 负责；EventTriggerService「有话才说」，
    # 所以固定话术的替身每次 tick 仍会发——真实场景由 due_line 节流。
    collected.clear()
    svc.tick(base + 60)
    assert any(e.title == "今日天气" for e in collected
               if isinstance(e, SpeakRequestedEvent))
