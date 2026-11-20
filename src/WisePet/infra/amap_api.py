"""高德周边搜索网关：nearby_pois 工具的后端实现。

用 Web 服务 API 的「周边搜索」（v3 place/around）按坐标查本地生活
POI（美食/咖啡/加油站…），返回 名称/地址/距离/电话。这是「附近xx」
类查询的正解——通用网页搜索（Bing/DDG）给的是城市级泛化结果，
拿不到具体的店铺列表。

Key：高德开放平台免费注册（lbs.amap.com），个人开发者每日有免费配额。
失败一律返回空列表并记日志，由工具层给人话兜底。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

AMAP_TIMEOUT_S = 10.0
_BASE = "https://restapi.amap.com/v3/place/around"
_GEOCODE_URL = "https://restapi.amap.com/v3/geocode/geo"


@dataclass(frozen=True)
class Poi:
    name: str
    address: str
    distance_m: int | None = None
    tel: str = ""


class AmapPoiGateway:
    """按坐标 + 关键词做周边搜索；key_provider 惰性取 Key（热改配置生效）。"""

    def __init__(self, key_provider):
        self._key_provider = key_provider

    def enabled(self) -> bool:
        try:
            return bool(str(self._key_provider() or "").strip())
        except Exception:  # noqa: BLE001 - 配置回调异常不影响主流程
            return False

    def geocode(self, address: str, city: str = "") -> tuple[float, float] | None:
        """把区域名/地址解析成 GCJ-02 坐标（经纬度）。

        用途：系统精确定位不可用时，用用户说的「桂林路」「一汽35街区」等
        区域名拿到街道级中心点，替代误差几公里的 IP 城市中心。失败返回 None。
        """
        key = str(self._key_provider() or "").strip()
        address = str(address or "").strip()[:60]
        if not key or not address:
            return None
        try:
            resp = httpx.get(
                _GEOCODE_URL,
                params={"key": key, "address": address, "city": city[:30]},
                timeout=AMAP_TIMEOUT_S,
                headers={"User-Agent": "WisePet/1.0"})
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001 - 地理编码失败静默回落
            logger.info("amap geocode failed: %s", exc)
            return None
        data = resp.json()
        if str(data.get("status") or "") != "1":
            logger.info("amap geocode rejected: %s", data.get("info"))
            return None
        geocodes = data.get("geocodes") or []
        if not geocodes:
            return None
        location = str(geocodes[0].get("location") or "")
        try:
            lon_s, lat_s = location.split(",", 1)
            return float(lat_s), float(lon_s)
        except (ValueError, TypeError):
            return None

    def search_around(self, keyword: str, lat: float, lon: float,
                      radius_m: int = 3000) -> list[Poi]:
        key = str(self._key_provider() or "").strip()
        if not key:
            raise ValueError("周边搜索需要在设置里填高德 Web 服务 Key")
        try:
            resp = httpx.get(
                _BASE,
                params={"key": key,
                        "location": f"{lon:.6f},{lat:.6f}",   # 高德: 经度,纬度
                        "keywords": keyword[:50],
                        "radius": max(500, min(int(radius_m), 10000)),
                        "offset": 8, "page": 1, "extensions": "base"},
                timeout=AMAP_TIMEOUT_S,
                headers={"User-Agent": "WisePet/1.0"})
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001 - 接口失败降级为空结果
            logger.info("amap place/around failed: %s", exc)
            return []
        data = resp.json()
        if str(data.get("status") or "") != "1":
            info = str(data.get("info") or "")
            logger.info("amap rejected: %s", info)
            if info.startswith("INVALID_USER_KEY"):
                raise ValueError("高德 Key 无效（检查设置里填的 Web 服务 Key）")
            return []
        return self._parse_pois(data)

    @staticmethod
    def _parse_pois(data: dict) -> list[Poi]:
        out: list[Poi] = []
        for item in (data.get("pois") or [])[:8]:
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            try:
                dist = int(str(item.get("distance") or "")) or None
            except ValueError:
                dist = None
            tel = item.get("tel") or ""
            tel = ",".join(tel) if isinstance(tel, list) else str(tel)
            out.append(Poi(name=name,
                           address=str(item.get("address") or "").strip(),
                           distance_m=dist, tel=tel[:40]))
        return out
