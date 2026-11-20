"""定位精度修复回归：诊断信息 / 区域名提取 / 地理编码 / nearby 精确中心。"""
from __future__ import annotations

from types import SimpleNamespace

from WisePet.app.agent_tools import AgentToolbox
from WisePet.domain.config_model import AppConfig
from WisePet.infra.amap_api import AmapPoiGateway
from WisePet.infra.geo_location import GeoInfo, GeoLocator


# ---------- 定位诊断 ----------

def test_geo_diagnosis_reports_ip_level_when_no_precise():
    geo = GeoLocator(precise_provider=None)
    # 注入 IP 端点替身：直接覆盖内部解析路径，避免真网络
    geo._locate_via_ip = lambda: GeoInfo(
        text="中国、吉林省、长春市（坐标 125.324,43.886）",
        city="长春市", lat=43.886, lon=125.324)
    info = geo.locate()
    assert info.precise is False
    diag = geo.last_diagnosis()
    assert "IP 城市级" in diag
    assert "系统精确定位未启用" in diag


def test_geo_diagnosis_reports_precise_level():
    geo = GeoLocator(precise_provider=lambda: (43.88, 125.32))
    geo._locate_via_ip = lambda: GeoInfo(text="长春", city="长春市",
                                         lat=43.886, lon=125.324)
    info = geo.locate()
    assert info.precise is True
    assert "系统精确定位" in geo.last_diagnosis()


# ---------- 区域名提取 ----------

def test_extract_area_from_keyword():
    assert AgentToolbox._extract_area("桂林路 咖啡") == "桂林路"
    assert AgentToolbox._extract_area("一汽35街区 美食") == "一汽35街区"
    assert AgentToolbox._extract_area("美食") == ""          # 纯类型词
    assert AgentToolbox._extract_area("朝阳区 火锅") == "朝阳区"
    assert AgentToolbox._extract_area("") == ""


# ---------- 高德地理编码 ----------

def test_amap_geocode_parses_location(monkeypatch):
    import WisePet.infra.amap_api as mod

    def fake_get(url, params=None, timeout=None, headers=None):
        assert "geocode" in url and params["address"] == "桂林路"
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
            "status": "1",
            "geocodes": [{"location": "125.320000,43.880000"}]})

    monkeypatch.setattr(mod.httpx, "get", fake_get)
    gw = AmapPoiGateway(key_provider=lambda: "test-key")
    result = gw.geocode("桂林路", city="长春市")
    assert result == (43.88, 125.32)


def test_amap_geocode_skips_on_failure(monkeypatch):
    import WisePet.infra.amap_api as mod

    def boom(*a, **k):
        raise RuntimeError("offline")

    monkeypatch.setattr(mod.httpx, "get", boom)
    gw = AmapPoiGateway(key_provider=lambda: "test-key")
    assert gw.geocode("桂林路") is None
    assert AmapPoiGateway(key_provider=lambda: "").geocode("桂林路") is None


# ---------- nearby_pois 用区域名精确中心 ----------

def _toolbox_with_nearby(geo_info, geocode_result, searched):
    geo = GeoLocator(precise_provider=None)
    geo.locate = lambda: geo_info
    amap = AmapPoiGateway(key_provider=lambda: "k")
    amap.geocode = lambda address, city="": geocode_result
    amap.search_around = lambda kw, lat, lon, radius: (
        searched.append((kw, lat, lon, radius))
        or [SimpleNamespace(name="某店", address="桂林路1号", distance_m=120, tel="")])
    cfg = AppConfig()
    tb = AgentToolbox(config_provider=lambda: cfg, geo=geo, amap=amap,
                      location_consent=lambda: True)
    return tb


def test_nearby_uses_area_geocode_when_ip_level():
    searches = []
    ip_info = GeoInfo(text="长春市中心", city="长春市",
                      lat=43.9, lon=125.3, precise=False)
    tb = _toolbox_with_nearby(ip_info, (43.88, 125.32), searches)
    text, _ = tb.execute("nearby_pois", {"keyword": "桂林路 咖啡"})
    # 用的是 geocode 的街道级坐标，不是 IP 城市中心 43.9/125.3
    assert searches[0][1:3] == (43.88, 125.32)
    assert "按区域名精确定位" in text


def test_nearby_keeps_ip_coords_when_precise_or_no_area():
    searches = []
    precise_info = GeoInfo(text="精确坐标", city="长春市",
                           lat=43.88, lon=125.32, precise=True)
    tb = _toolbox_with_nearby(precise_info, None, searches)
    tb.execute("nearby_pois", {"keyword": "美食"})   # 纯类型词无区域
    assert searches[0][1:3] == (43.88, 125.32)
