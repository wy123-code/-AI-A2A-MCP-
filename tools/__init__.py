"""工具注册中心 —— 统一从 agents/worker/*/tool.py 导入，保持 TOOL_REGISTRY 向后兼容。"""
from typing import Any, Callable, Coroutine, Dict, List, Optional

from agents.worker.ticket.tool import query_ticket
from agents.worker.train_ticket.tool import query_train_ticket
from agents.worker.weather.tool import weather_tool
from agents.worker.tour_group.tool import tour_group_tool
from agents.worker.hotel.tool import hotel_tool
from agents.worker.car_rental.tool import car_rental_tool
from agents.worker.insurance.tool import insurance_tool
from agents.worker.attraction.tool import recommend_attractions
from agents.worker.flight.tool import flight_tool

ToolFunc = Callable[..., Coroutine[Any, Any, Dict[str, Any]]]

# 工具描述元数据（供注册中心、/intents 端点、前端动态渲染使用）
_TOOL_METADATA: Dict[str, Dict] = {
    "ticket_query":           {"name": "ticket_query",           "intent": "ship_ticket",         "description": "船票/演唱会票查询"},
    "train_ticket_query":     {"name": "train_ticket_query",     "intent": "train_ticket",        "description": "火车票查询 (12306)"},
    "weather_query":          {"name": "weather_query",          "intent": "weather_query",       "description": "天气查询 (和风天气API)"},
    "tour_group_query":       {"name": "tour_group_query",       "intent": "tour_group_query",    "description": "旅行团查询"},
    "hotel_query":            {"name": "hotel_query",            "intent": "hotel_query",         "description": "酒店查询"},
    "car_rental_query":       {"name": "car_rental_query",       "intent": "car_rental_query",    "description": "租车查询"},
    "insurance_query":        {"name": "insurance_query",        "intent": "insurance_query",     "description": "保险查询"},
    "attraction_recommend":   {"name": "attraction_recommend",   "intent": "attraction_recommend","description": "景点推荐 (Milvus向量检索)"},
    "flight_query":           {"name": "flight_query",           "intent": "flight_ticket",       "description": "飞机票查询 (MySQL实时数据)"},
}

# 核心注册表：工具名 → 异步函数
TOOL_REGISTRY: Dict[str, ToolFunc] = {
    "ticket_query": query_ticket,
    "train_ticket_query": query_train_ticket,
    "weather_query": weather_tool,
    "tour_group_query": tour_group_tool,
    "hotel_query": hotel_tool,
    "car_rental_query": car_rental_tool,
    "insurance_query": insurance_tool,
    "attraction_recommend": recommend_attractions,
    "flight_query": flight_tool,
}


def register_tool(name: str, func: ToolFunc, metadata: Dict = None):
    """动态注册工具 —— 支持热插拔，无需修改主流程代码。"""
    TOOL_REGISTRY[name] = func
    if metadata:
        _TOOL_METADATA[name] = metadata


def get_tool(name: str) -> Optional[ToolFunc]:
    """获取工具函数。"""
    return TOOL_REGISTRY.get(name)


def list_tools() -> List[Dict]:
    """列出所有已注册工具的元数据。"""
    result = []
    for name, func in TOOL_REGISTRY.items():
        meta = _TOOL_METADATA.get(name, {"name": name})
        result.append({
            "name": name,
            "intent": meta.get("intent", ""),
            "description": meta.get("description", ""),
            "required_slots": meta.get("required_slots", []),
        })
    return result


def get_tool_routing() -> Dict[str, str]:
    """从注册表派生意图 → 工具名路由表 (替代 config.py 中的硬编码 TOOL_ROUTING)。"""
    return {
        meta["intent"]: name
        for name, meta in _TOOL_METADATA.items()
        if meta.get("intent")
    }


def get_supported_intents() -> List[str]:
    """从注册表派生支持的意图列表 (替代 config.py 中的硬编码 SUPPORTED_INTENTS)。"""
    return [meta["intent"] for meta in _TOOL_METADATA.values() if meta.get("intent")]
