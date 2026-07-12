"""天气查询工具 —— 通过和风天气 v30 API 获取实时天气 + 7 日预报。"""

import asyncio
from datetime import date, timedelta
from typing import Any, Dict

import aiohttp
from loguru import logger

from config import WEATHER_API_CONFIG, MCP_SERVERS


async def _get_real_today() -> date:
    """从 12306 MCP 获取当前真实日期，失败则回退到系统时钟。"""
    try:
        from mcp.mcp_client import get_mcp_client
        mcp_config = MCP_SERVERS.get("12306-mcp")
        if mcp_config:
            client = await get_mcp_client("12306-mcp", mcp_config["url"])
            result = await client.call_tool("get-current-date", {})
            if result.get("success"):
                raw = str(result.get("data", "")).strip()
                import re
                m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", raw)
                if m:
                    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except Exception:
        pass
    return date.today()


def _parse_date(date_str: str, today: date) -> str:
    """将日期字符串标准化为 yyyy-MM-dd。"""
    if not date_str:
        return today.strftime("%Y-%m-%d")

    date_str = date_str.strip()

    relative = {
        "今天": today, "今日": today,
        "明天": today + timedelta(days=1), "明日": today + timedelta(days=1),
        "后天": today + timedelta(days=2), "後天": today + timedelta(days=2),
        "大后天": today + timedelta(days=3),
        "昨天": today - timedelta(days=1), "昨日": today - timedelta(days=1),
    }
    if date_str in relative:
        return relative[date_str].strftime("%Y-%m-%d")

    import re
    m = re.search(r"(\d{4})\s*[-/]\s*(\d{1,2})\s*[-/]\s*(\d{1,2})", date_str)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?", date_str)
    if m:
        return f"{today.year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"

    return today.strftime("%Y-%m-%d")


async def _fetch_json(session: aiohttp.ClientSession, url: str,
                      params: dict, headers: dict) -> dict:
    """发起 GET 请求并返回 JSON，失败时返回空 dict。"""
    try:
        async with session.get(url, params=params, headers=headers) as resp:
            if resp.status != 200:
                text = await resp.text()
                logger.error(f"Weather API {resp.status}: {text[:200]}")
                return {}
            return await resp.json()
    except Exception as e:
        logger.error(f"Weather API request failed: {e}")
        return {}


def _is_travel_query(query: str) -> bool:
    """判断用户是否在做出行/旅游规划类查询（控制是否输出预报和景点）。"""
    keywords = ["去玩", "旅游", "行程", "规划", "推荐", "攻略", "出行", "游玩",
                "旅行", "出游", "度假", "几日游", "几天", "玩几天", "行程安排"]
    return any(k in query for k in keywords)


async def weather_tool(intent: str, slots: Dict[str, Any]) -> Dict[str, Any]:
    """通过和风天气 API 查询指定城市的实时天气 + 预报。

    仅当用户有出行/旅游规划意图时才附带未来预报和景点信息，
    单纯查天气只返回当天数据。
    """
    city = str(slots.get("city", "")).strip()
    user_query = str(slots.get("_query", ""))

    real_today = await _get_real_today()
    query_date = _parse_date(str(slots.get("date", "")).strip(), real_today)

    if not city:
        return {"success": False, "error": "请提供要查询的城市名称", "data": []}

    from tools.city_codes import get_location_id
    location_id = get_location_id(city)
    if not location_id:
        return {
            "success": False,
            "error": f"暂不支持查询城市「{city}」的天气，请尝试使用其他城市名",
            "data": [],
        }

    cfg = WEATHER_API_CONFIG
    base = cfg["base_url"]
    key = cfg["key"]

    if not key:
        return {"success": False, "error": "天气服务未配置API密钥，请联系管理员设置 WEATHER_API_KEY", "data": []}

    common_params = {"location": location_id}
    headers = {"X-QW-Api-Key": key}

    logger.info(f"WeatherTool: {city}({location_id}) real_today={real_today} query_date={query_date}")

    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # 并发请求实时天气 + 30 日预报
            now_url = f"{base}{cfg['now_path']}"
            fc_url = f"{base}{cfg['forecast_path']}"
            now_raw, fc_raw = await asyncio.gather(
                _fetch_json(session, now_url, common_params, headers),
                _fetch_json(session, fc_url, common_params, headers),
            )

        # ---------- 校验 API 响应 ----------
        if not now_raw and not fc_raw:
            return {"success": False, "error": "天气API请求失败，请检查网络或API密钥配置", "data": []}

        # ---------- 解析实时天气 ----------
        now_data = now_raw.get("now", {}) if now_raw.get("code") == "200" else {}
        update_time = now_raw.get("updateTime", "")

        # ---------- 解析 7 日预报 ----------
        daily = fc_raw.get("daily", []) if fc_raw.get("code") == "200" else []

        # 匹配目标日期的预报
        qdate = date.fromisoformat(query_date)
        day_offset = (qdate - real_today).days
        if 0 <= day_offset < len(daily):
            forecast_target = daily[day_offset]
        elif daily:
            forecast_target = daily[0]
        else:
            forecast_target = {}

        # ---------- 构建统一响应 ----------
        weather_data = {
            "city": city,
            "location_id": location_id,
            "date": query_date,
            "update_time": update_time,
            # 实时天气
            "temp_now": now_data.get("temp", ""),
            "feels_like": now_data.get("feelsLike", ""),
            "weather_text": now_data.get("text", ""),
            "wind_direction": now_data.get("windDir", ""),
            "wind_scale": now_data.get("windScale", ""),
            "humidity": now_data.get("humidity", ""),
            "precip": now_data.get("precip", ""),
            "pressure": now_data.get("pressure", ""),
            "visibility": now_data.get("vis", ""),
            # 预报中的当天最高/最低/天气
            "temp_high": forecast_target.get("tempMax", ""),
            "temp_low": forecast_target.get("tempMin", ""),
            "day_weather": forecast_target.get("textDay", ""),
            "night_weather": forecast_target.get("textNight", ""),
            "uv_index": forecast_target.get("uvIndex", ""),
            "sunrise": forecast_target.get("sunrise", ""),
            "sunset": forecast_target.get("sunset", ""),
        }

        # ---------- 7 日预报（仅出行规划类查询） ----------
        if _is_travel_query(user_query):
            forecast = []
            for i, d in enumerate(daily[:3]):
                forecast.append({
                    "date": (real_today + timedelta(days=i)).strftime("%Y-%m-%d"),
                    "high": d.get("tempMax", ""),
                    "low": d.get("tempMin", ""),
                    "day": d.get("textDay", ""),
                    "night": d.get("textNight", ""),
                })
            weather_data["forecast"] = forecast

        logger.info(
            f"WeatherTool: {city} now={now_data.get('temp', '?')}°C {now_data.get('text', '?')}, "
            f"high={forecast_target.get('tempMax', '?')} low={forecast_target.get('tempMin', '?')}"
        )
        return {"success": True, "error": None, "data": weather_data}

    except aiohttp.ClientError as e:
        logger.error(f"WeatherTool: network error: {e}")
        return {"success": False, "error": f"天气服务网络请求失败: {e}", "data": []}
    except Exception as e:
        logger.error(f"WeatherTool: unexpected error: {e}")
        return {"success": False, "error": f"天气查询异常: {e}", "data": []}
