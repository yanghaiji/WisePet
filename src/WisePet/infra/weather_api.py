"""天气网关：免 Key 实况，供聊天问答与主动天气推送。

**双源策略（v1.35 实测修正）**：
- 主源 `t.weather.itboy.net`（中国天气网数据，国内 CDN，0.5s 稳通、免 Key）：
  按城市代码查，返回当前温度 + 今日天气；内置常见城市代码表 + 名称匹配。
- 备源 Open-Meteo（geocoding 拿坐标 → forecast 查实况）：海外/未收录城市兜底。

实测：本机网络下 api.open-meteo.com 的 forecast 端点 SSL 握手超时（geocoding
端点却通），wttr.in 自签证书验证失败；国内源稳定可用。故调整为国内源优先。

网络/解析失败一律返回 None，由上游静默跳过——天气是「有更好」的增强，
绝不能阻塞桌宠主流程，断网时照常工作。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .geo_location import GeoLocator
from .web_search import _HttpClient

logger = logging.getLogger(__name__)

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ITBOY_URL = "http://t.weather.itboy.net/api/weather/city/{code}"
WEATHER_TIMEOUT_S = 8.0

#: 中国天气网城市代码（常见城市；键为可匹配的城市名片段 → 代码）
#: 覆盖省会/直辖市/计划单列市，未收录城市回落 Open-Meteo 备源。
_CITY_CODES: dict[str, str] = {
    "北京": "101010100", "上海": "101020100", "天津": "101030100", "重庆": "101040100",
    "石家庄": "101090101", "太原": "101100101", "呼和浩特": "101080101",
    "沈阳": "101070101", "长春": "101060101", "哈尔滨": "101050101",
    "南京": "101190101", "杭州": "101210101", "合肥": "101220101", "福州": "101230101",
    "南昌": "101240101", "济南": "101120101", "郑州": "101180101", "武汉": "101200101",
    "长沙": "101250101", "广州": "101280101", "深圳": "101280601", "南宁": "101300101",
    "海口": "101310101", "成都": "101270101", "贵阳": "101260101", "昆明": "101290101",
    "拉萨": "101140101", "西安": "101110101", "兰州": "101160101", "西宁": "101150101",
    "银川": "101170101", "乌鲁木齐": "101130101", "大连": "101070201", "青岛": "101120201",
    "宁波": "101210401", "厦门": "101230201", "苏州": "101190401", "无锡": "101190201",
    "东莞": "101281601", "佛山": "101280800", "珠海": "101280701", "常州": "101191101",
}

#: itboy 的天气文字 → WMO code（复用 domain.weather 的中文描述口径）
_TEXT_TO_CODE: tuple[tuple[tuple[str, ...], int], ...] = (
    (("雷",), 95), (("暴",), 95), (("雪",), 71), (("雨夹雪",), 71),
    (("大雨",), 65), (("中雨",), 63), (("小雨",), 61), (("阵雨",), 80),
    (("雨",), 61), (("雾",), 45), (("霾",), 45),
    (("阴",), 3), (("多云",), 2), (("晴",), 0),
)


def _text_to_code(text: str) -> int:
    for keys, code in _TEXT_TO_CODE:
        if any(k in text for k in keys):
            return code
    return 2


@dataclass(frozen=True)
class WeatherSnapshot:
    """一次天气实况：城市 + WMO code + 温度 + 湿度/风力/时段温度等细节。"""
    city: str = ""
    code: int = 0
    temp: float = 0.0
    humidity: str = ""      # 如 "45%"
    wind: str = ""          # 如 "西北风 1级"
    high: str = ""          # 今日高温，如 "高温 4℃"
    low: str = ""           # 今日低温
    notice: str = ""        # 生活提示
    observed_at: str = ""   # 观测/更新时间，如 "12:57"


class WeatherGateway:
    """注入定位器；location_provider / http_get 可覆盖以便离线测试。"""

    def __init__(self, geo: GeoLocator | None = None,
                 location_provider=None, http_get=None):
        self._geo = geo
        self._location = location_provider
        self._http_get = http_get

    def _get_json(self, url: str, params: dict | None = None) -> dict:
        if self._http_get is not None:
            resp = self._http_get(url, params=params, timeout=WEATHER_TIMEOUT_S)
            return resp.json()
        # 复用 web_search 的进程级客户端（合并信任链，兼容代理拦截）
        resp = _HttpClient.get().get(url, params=params, timeout=WEATHER_TIMEOUT_S)
        resp.raise_for_status()
        return resp.json()

    def _city(self) -> str:
        if self._location is not None:
            info = self._location()
        elif self._geo is not None:
            try:
                info = self._geo.locate()
            except Exception:  # noqa: BLE001 - 定位失败静默回落
                logger.debug("weather locate failed", exc_info=True)
                info = None
        else:
            info = None
        if info is None:
            raise ValueError("no location available")
        return str(getattr(info, "city", "") or "")

    def _from_itboy(self, city: str) -> WeatherSnapshot | None:
        """国内源：城市名匹配代码 → 查实况。未收录城市返回 None。"""
        code = next((v for k, v in _CITY_CODES.items() if k in city), "")
        if not code:
            return None
        data = self._get_json(ITBOY_URL.format(code=code))
        d = data.get("data") or {}
        city_info = data.get("cityInfo") or {}
        forecast = d.get("forecast") or []
        today = forecast[0] if forecast else {}
        text = str(today.get("type") or "")
        temp = d.get("wendu")
        if not text and temp in (None, ""):
            return None
        wind = " ".join(p for p in (today.get("fx"), today.get("fl")) if p)
        return WeatherSnapshot(
            city=str(city_info.get("city") or city),
            code=_text_to_code(text),
            temp=float(temp) if temp not in (None, "") else 0.0,
            humidity=str(d.get("shidu") or ""),
            wind=str(wind),
            high=str(today.get("high") or ""),
            low=str(today.get("low") or ""),
            notice=str(today.get("notice") or ""),
            observed_at=str(city_info.get("updateTime") or ""))

    def _from_open_meteo(self, city: str) -> WeatherSnapshot | None:
        """备源：城市名 geocoding → 坐标 → forecast（部分网络下 forecast 不通）。"""
        data = self._get_json(GEOCODE_URL, {"name": city, "count": 1,
                                            "language": "zh", "format": "json"})
        results = data.get("results") or []
        if not results:
            return None
        top = results[0]
        lat, lon = float(top["latitude"]), float(top["longitude"])
        name = str(top.get("name") or city)
        fc = self._get_json(FORECAST_URL, {
            "latitude": lat, "longitude": lon,
            "current": "temperature_2m,weather_code,relative_humidity_2m",
            "timezone": "auto"})
        current = fc.get("current") or {}
        hum = current.get("relative_humidity_2m")
        return WeatherSnapshot(
            city=name,
            code=int(current.get("weather_code", 0)),
            temp=float(current.get("temperature_2m", 0.0)),
            humidity=f"{int(hum)}%" if isinstance(hum, (int, float)) else "",
            observed_at=str(current.get("time") or "")[11:16])

    def fetch(self) -> WeatherSnapshot | None:
        """取一条实况；国内源优先、Open-Meteo 备源，全失败返回 None。"""
        try:
            city = self._city()
        except Exception as exc:
            logger.info("weather skip: no location: %s", exc)
            return None
        if not city:
            logger.info("weather skip: empty city")
            return None
        for source in (self._from_itboy, self._from_open_meteo):
            try:
                snap = source(city)
                if snap is not None:
                    return snap
            except Exception as exc:  # noqa: BLE001 - 单源失败试下一个
                logger.info("weather source %s failed: %s", source.__name__, exc)
        return None
