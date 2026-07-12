"""工具执行节点 —— 根据意图路由到对应工具并调用执行，含 Redis 缓存层。"""

from loguru import logger
from config import TOOL_ROUTING
from tools import TOOL_REGISTRY
from graph.state import TourismStateDict
from cache.query_cache import get_query_cache


async def tool_execution_node(state: TourismStateDict) -> TourismStateDict:
    intent = state.get("intent", "")
    slots = dict(state.get("slots", {}))
    slots["_query"] = state.get("query", "")

    tool_name = TOOL_ROUTING.get(intent, "")
    if not tool_name:
        logger.error(f"ToolExecution: no tool mapped for intent={intent}")
        state["error"] = f"No tool for intent: {intent}"
        state["tool_result"] = {"success": False, "error": state["error"], "data": []}
        state["next_step"] = "result_summary"
        return state

    tool_func = TOOL_REGISTRY.get(tool_name)
    if not tool_func:
        logger.error(f"ToolExecution: tool not found in registry: {tool_name}")
        state["error"] = f"Tool not found: {tool_name}"
        state["tool_result"] = {"success": False, "error": state["error"], "data": []}
        state["next_step"] = "result_summary"
        return state

    cache = await get_query_cache()
    cache_params = {k: v for k, v in slots.items() if k != "_query"}
    cached = await cache.get(tool_name, cache_params)
    if cached is not None:
        logger.info("ToolExecution: cache HIT", tool=tool_name, intent=intent)
        state["tool_name"] = tool_name
        state["tool_result"] = cached
        state["next_step"] = "result_summary"
        return state

    logger.info("ToolExecution: calling {}", tool_name, intent=intent)
    try:
        result = await tool_func(intent=intent, slots=slots)
        logger.info("ToolExecution: {} returned success={}", tool_name, result.get("success"))
    except Exception as e:
        logger.error(f"ToolExecution: {tool_name} failed: {e}")
        result = {"success": False, "error": str(e), "data": []}

    if result.get("success"):
        await cache.set(tool_name, cache_params, result)

    state["tool_name"] = tool_name
    state["tool_result"] = result
    state["next_step"] = "result_summary"
    return state
