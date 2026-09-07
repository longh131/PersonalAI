"""Bounded adapters: weather and A-share daily bars. Unsupported kinds refuse clearly."""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timedelta
from typing import Any

import httpx

WEATHER_MARKERS = ("天气", "气温", "下雨", "下雪", "多少度", "热不热", "冷不冷")
STOCK_CODE = re.compile(r"\b(\d{6})(?:\.(SH|SZ|BJ))?\b", re.IGNORECASE)


def looks_like_weather(text: str) -> bool:
    return any(token in (text or "") for token in WEATHER_MARKERS)


def refuse(reason: str, need: str, nxt: str) -> str:
    return f"做不到：{reason}\n差什么：{need}\n你可以：{nxt}"


def secret(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def extract_city(text: str) -> str:
    raw = (text or "").strip()
    for token in WEATHER_MARKERS:
        raw = raw.replace(token, " ")
    raw = re.sub(r"(怎么样|如何|帮我查|查询|今天|现在|当地|本地)", " ", raw)
    city = re.sub(r"\s+", "", raw)
    if 1 < len(city) <= 8 and not city.isdigit():
        return city
    return ""


def extract_stock_code(text: str) -> str:
    match = STOCK_CODE.search(text or "")
    if not match:
        return ""
    code = match.group(1)
    suffix = (match.group(2) or "").upper()
    if suffix:
        return f"{code}.{suffix}"
    if code.startswith("6"):
        return f"{code}.SH"
    if code.startswith(("0", "3")):
        return f"{code}.SZ"
    if code.startswith(("4", "8", "9")):
        return f"{code}.BJ"
    return f"{code}.SH"


async def fetch_weather(query: str) -> str:
    """QWeather if a key exists; otherwise Open-Meteo. City first, then IP, then ask."""
    city = extract_city(query)
    key = secret("QWEATHER_API_KEY") or secret("HEWEATHER_KEY")
    if key:
        return await _qweather(city, key)
    return await _open_meteo(city)


async def _qweather(city: str, key: str) -> str:
    location = city or await _ip_city()
    if not location:
        return refuse("还不知道报哪个城市", "城市名，或允许按网络位置估计", "说「北京天气怎么样」，或再说一次「当地天气」")
    async with httpx.AsyncClient(timeout=10.0) as client:
        geo = await client.get(
            "https://geoapi.qweather.com/v2/city/lookup",
            params={"location": location, "key": key},
        )
        geo.raise_for_status()
        places = (geo.json().get("location") or [])
        if not places:
            return refuse("和风找不到这个地点", f"城市「{location}」", "换一个城市名再试")
        loc_id = places[0].get("id")
        name = places[0].get("name") or location
        now = await client.get(
            "https://devapi.qweather.com/v7/weather/now",
            params={"location": loc_id, "key": key},
        )
        now.raise_for_status()
        data = (now.json().get("now") or {})
    if not data:
        return refuse("和风没有返回实况", "接口是否开通、Key 是否有效", "到和风控制台检查 Key，再说「确认」重写密钥")
    return (
        f"{name}现在 {data.get('text', '')}，{data.get('temp', '?')}℃，"
        f"体感 {data.get('feelsLike', '?')}℃，风 {data.get('windDir', '')}{data.get('windScale', '')}级。"
    )


async def _open_meteo(city: str) -> str:
    location = city
    lat = lon = None
    label = location
    async with httpx.AsyncClient(timeout=10.0) as client:
        if not location:
            lat, lon, label = await _ip_latlon(client)
            if lat is None:
                return refuse(
                    "没有天气 Key，也没能按网络位置定位",
                    "城市名，或在 .env 写入 QWEATHER_API_KEY（确认后我可以代写）",
                    "说「上海天气怎么样」，或申请和风 Key 后说「确认写入密钥」",
                )
        else:
            geo = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": location, "count": 1, "language": "zh"},
            )
            geo.raise_for_status()
            results = geo.json().get("results") or []
            if not results:
                return refuse("Open-Meteo 找不到这个地点", f"城市「{location}」", "换常用中文或拼音城市名")
            lat = results[0]["latitude"]
            lon = results[0]["longitude"]
            label = results[0].get("name") or location
        wx = await client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,weather_code,wind_speed_10m,relative_humidity_2m",
                "timezone": "auto",
            },
        )
        wx.raise_for_status()
        current = wx.json().get("current") or {}
    if not current:
        return refuse("Open-Meteo 没有实况", "国际预报服务是否可访问", "改用和风 Key，或稍后再试")
    return (
        f"{label}现在约 {current.get('temperature_2m', '?')}℃，"
        f"湿度 {current.get('relative_humidity_2m', '?')}%，"
        f"风速 {current.get('wind_speed_10m', '?')} km/h。"
        f"{'' if city else '（按网络位置估计，可能不准）'}"
    )


async def _ip_city() -> str:
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            _, _, city = await _ip_latlon(client)
            return city or ""
    except Exception:
        return ""


async def _ip_latlon(client: httpx.AsyncClient) -> tuple[float | None, float | None, str]:
    try:
        resp = await client.get("http://ip-api.com/json/", params={"fields": "status,city,lat,lon"})
        data = resp.json()
        if data.get("status") != "success":
            return None, None, ""
        return float(data["lat"]), float(data["lon"]), str(data.get("city") or "")
    except Exception:
        return None, None, ""


async def fetch_stock_daily(query: str, days: int = 180) -> str:
    """A-share daily close/volume. Tushare if token; else akshare if installed."""
    code = extract_stock_code(query)
    if not code:
        return refuse(
            "还没有股票代码，不能当行情软件猜简称",
            "6 位代码，如 600519 或 600519.SH",
            "再说一遍带代码的需求，例如「600519 近半年收盘价成交额」",
        )
    token = secret("TUSHARE_TOKEN")
    if token:
        return await asyncio.to_thread(_tushare_daily, code, days, token)
    try:
        import akshare as ak  # type: ignore[import-untyped]
    except ImportError:
        return refuse(
            "还没接上日线源",
            "TUSHARE_TOKEN，或确认后安装白名单包 akshare",
            "申请 Tushare token 后说「确认写入 TUSHARE_TOKEN」，或说「确认安装 akshare」",
        )
    return await asyncio.to_thread(_akshare_daily, code, days, ak)


def _tushare_daily(code: str, days: int, token: str) -> str:
    try:
        import tushare as ts  # type: ignore[import-untyped]
    except ImportError:
        return refuse(
            "有 Token 但没装 tushare",
            "本机 venv 里的 tushare 包",
            "说「确认安装 tushare」",
        )
    end = datetime.now()
    start = end - timedelta(days=max(days, 30))
    pro = ts.pro_api(token)
    frame = pro.daily(
        ts_code=code,
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
    )
    if frame is None or getattr(frame, "empty", True):
        return refuse("Tushare 没有这只股票的日线", f"代码 {code} 或积分权限", "核对代码，或换 akshare")
    frame = frame.sort_values("trade_date")
    first = frame.iloc[0]
    last = frame.iloc[-1]
    change = (float(last["close"]) / float(first["close"]) - 1.0) * 100
    return (
        f"{code} {first['trade_date']}→{last['trade_date']}："
        f"收盘 {first['close']}→{last['close']}，区间 {change:.1f}%；"
        f"最近一日成交额 {last.get('amount', '?')}（千元）。"
    )


def _akshare_daily(code: str, days: int, ak: Any) -> str:
    symbol = code.split(".")[0]
    end = datetime.now()
    start = end - timedelta(days=max(days, 30))
    frame = ak.stock_zh_a_hist(
        symbol=symbol,
        period="daily",
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
        adjust="",
    )
    if frame is None or getattr(frame, "empty", True):
        return refuse("akshare 没有日线", f"代码 {symbol}", "核对代码或改用 Tushare")
    first = frame.iloc[0]
    last = frame.iloc[-1]
    close0 = float(first["收盘"])
    close1 = float(last["收盘"])
    change = (close1 / close0 - 1.0) * 100
    return (
        f"{symbol} {first['日期']}→{last['日期']}："
        f"收盘 {close0}→{close1}，区间 {change:.1f}%；"
        f"最近一日成交额 {last.get('成交额', '?')}。"
    )


async def run_kind(kind: str, query: str) -> str:
    wanted = (kind or "auto").strip().lower()
    if wanted in {"auto", "自动"}:
        if looks_like_weather(query):
            wanted = "weather"
        elif extract_stock_code(query) or any(token in query for token in ("收盘", "成交额", "日线", "行情")):
            wanted = "stock"
        else:
            return refuse(
                "没有对应的现成调用模块",
                "已支持：天气（和风 Key 或 Open-Meteo）、A 股日线（Tushare 或 akshare）",
                "先用 find_capability 找路；若源已支持，写入 Key 或确认安装后再问",
            )
    if wanted in {"weather", "天气"}:
        return await fetch_weather(query)
    if wanted in {"stock", "行情", "日线"}:
        return await fetch_stock_daily(query)
    return refuse(
        f"没有「{kind}」这类适配器",
        "当前软硬件只接了天气和 A 股日线",
        "换一个已支持的需求，或找路后等我加适配器（不是自我改内核）",
    )
