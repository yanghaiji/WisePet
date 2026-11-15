"""位置感知：IP 城市级定位（get_location / nearby_pois 工具的后端实现）。

桌面端没有浏览器的 geolocation API；Windows 系统级精确定位需要额外
运行时权限与商店签名，对桌宠场景过重。这里用「IP 归属地」方案：
免 Key、无系统权限弹窗，精确到城市级（足够支撑「附近美食/天气」类查询）。

隐私边界：
- 只有 agent 调用相关工具且用户在弹窗里授权（或设置里选了总是允许）
  才会真正发请求；授权状态持久化在 config.json 的 location.consent；
- 结果在本进程缓存 6 小时，避免每轮对话都打一次外部接口。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

GEO_TIMEOUT_S = 8.0
#: IP 定位缓存（秒）：城市级位置半天不会变，缓存到 6 小时
GEO_CACHE_TTL_S = 6 * 3600
#: 系统精确定位缓存（秒）：人在动，10 分钟内的位置还算可信
PRECISE_CACHE_TTL_S = 10 * 60
#: 系统定位失败后的退避（秒）：Windows 位置服务没开时，别每轮对话都干等超时
PRECISE_FAIL_BACKOFF_S = 10 * 60


@dataclass(frozen=True)
class GeoInfo:
    """一次定位结果：给人看的一句话 + 给接口用的坐标/城市。"""
    text: str                       # 如「中国、吉林省、长春市（坐标 125.324,43.886）」
    city: str = ""                  # 城市名（如 长春市），可能为空
    lat: float | None = None
    lon: float | None = None
    precise: bool = False           # True=系统定位（百米级）；False=IP 城市


def wgs84_to_gcj02(lat: float, lon: float) -> tuple[float, float]:
    """WGS-84 → GCJ-02（火星坐标）：国内地图服务（高德）要求 GCJ-02。

    标准偏移算法；海外坐标原样返回。误差约 1~2 米，对附近搜索足够。
    """
    import math
    if not (3.0 < lat < 54.0 and 72.0 < lon < 136.0):   # 中国大陆粗判
        return lat, lon
    a = 6378245.0
    ee = 0.00669342162296594323

    def _tlat(x: float, y: float) -> float:
        ret = (-100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y
               + 0.2 * math.sqrt(abs(x)))
        ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(
            2.0 * x * math.pi)) * 2.0 / 3.0
        ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(
            y / 3.0 * math.pi)) * 2.0 / 3.0
        ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(
            y * math.pi / 30.0)) * 2.0 / 3.0
        return ret

    def _tlon(x: float, y: float) -> float:
        ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
        ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(
            2.0 * x * math.pi)) * 2.0 / 3.0
        ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(
            x / 3.0 * math.pi)) * 2.0 / 3.0
        ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(
            x / 30.0 * math.pi)) * 2.0 / 3.0
        return ret

    dlat = _tlat(lon - 105.0, lat - 35.0)
    dlon = _tlon(lon - 105.0, lat - 35.0)
    radlat = lat / 180.0 * math.pi
    magic = math.sin(radlat)
    magic = 1 - ee * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((a * (1 - ee)) / (magic * sqrtmagic) * math.pi)
    dlon = (dlon * 180.0) / (a / sqrtmagic * math.cos(radlat) * math.pi)
    return lat + dlat, lon + dlon


class GeoLocator:
    """定位器：优先系统精确定位（若注入了 provider），失败回落 IP 城市。"""

    def __init__(self, precise_provider=None):
        #: precise_provider: 主线程异步取系统定位的回调，返回 (lat, lon) 或 None
        self._precise_provider = precise_provider
        self._cache_ts: float = 0.0
        self._cache: GeoInfo | None = None
        self._ip_city: str = ""          # 精确定位无城市名，用最近一次 IP 城市补
        self._precise_fail_ts: float = 0.0

    def locate(self) -> GeoInfo | None:
        now = time.monotonic()
        if self._cache is not None:
            ttl = (PRECISE_CACHE_TTL_S if self._cache.precise
                   else GEO_CACHE_TTL_S)
            if now - self._cache_ts < ttl:
                return self._cache
        if self._precise_provider is not None \
                and now - self._precise_fail_ts > PRECISE_FAIL_BACKOFF_S:
            pos = self._safe_precise()
            self._precise_fail_ts = now
            if pos:
                lat, lon = wgs84_to_gcj02(*pos)
                if not self._ip_city:   # 首次精确定位缺城市名：补一次 IP 归属地
                    ipinfo = self._locate_via_ip()
                    if ipinfo:
                        self._ip_city = ipinfo.city or self._ip_city
                city = self._ip_city
                where = f"{city}（精确坐标 {lat:.4f},{lon:.4f}，误差约百米）" \
                    if city else f"精确坐标 {lat:.4f},{lon:.4f}（误差约百米）"
                info = GeoInfo(text=where, city=city, lat=lat, lon=lon,
                               precise=True)
                self._cache_ts = now
                self._cache = info
                return info
        info = self._locate_via_ip()
        if info:
            self._ip_city = info.city or self._ip_city
            self._cache_ts = now
            self._cache = info
        return info

    def _safe_precise(self):
        try:
            return self._precise_provider()
        except Exception:  # noqa: BLE001 - 定位回调异常不崩主流程
            logger.exception("precise provider failed")
            return None

    def _locate_via_ip(self) -> GeoInfo | None:
        endpoints = (
            # ip-api 免费端点仅支持 http（https 要付费），字段够用且国内可达
            ("http://ip-api.com/json/?lang=zh-CN&fields="
             "status,country,regionName,city,lat,lon,isp",
             self._parse_ip_api),
            ("https://ipapi.co/json/", self._parse_ipapi_co),
        )
        for url, parser in endpoints:
            try:
                resp = httpx.get(url, timeout=GEO_TIMEOUT_S,
                                 headers={"User-Agent": "WisePet/1.0"})
                resp.raise_for_status()
                info = parser(resp.json())
                if info:
                    return info
            except Exception as exc:  # noqa: BLE001 - 逐端点降级
                logger.info("geo endpoint %s failed: %s", url.split('/')[2], exc)
        return None

    @staticmethod
    def _dedupe(*parts: str) -> list[str]:
        """地区串去重（国家可能与省市重复，如「中国」），保持顺序。"""
        ordered: list[str] = []
        for p in parts:
            if p and p not in ordered:
                ordered.append(p)
        return ordered

    @staticmethod
    def _with_coords(text: str, lat, lon) -> str:
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            text += f"（坐标 {float(lat):.3f},{float(lon):.3f}）"
        return text

    @classmethod
    def _parse_ip_api(cls, data: dict) -> GeoInfo | None:
        if str(data.get("status") or "") != "success":
            return None
        city = str(data.get("city") or "").strip()
        region = str(data.get("regionName") or "").strip()
        country = str(data.get("country") or "").strip()
        if not city and not region:
            return None
        parts = cls._dedupe(country, region, city)
        lat, lon = data.get("lat"), data.get("lon")
        text = cls._with_coords("、".join(parts), lat, lon)
        return GeoInfo(text=text or None, city=city or region, lat=lat, lon=lon)

    @classmethod
    def _parse_ipapi_co(cls, data: dict) -> GeoInfo | None:
        city = str(data.get("city") or "").strip()
        region = str(data.get("region") or "").strip()
        country = str(data.get("country_name") or "").strip()
        if not city and not region:
            return None
        parts = cls._dedupe(country, region, city)
        lat, lon = data.get("latitude"), data.get("longitude")
        text = cls._with_coords("、".join(parts), lat, lon)
        return GeoInfo(text=text or None, city=city or region, lat=lat, lon=lon)
