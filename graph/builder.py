"""Agent 管线编排 —— 优先使用 Orchestrator Agent（A2A + MCP 多智能体架构）。

本文件作为兼容层：
- 当多智能体系统就绪时：委托给 OrchestratorAgent（MCP Pub/Sub 通信）
- 当多智能体未初始化时（测试/开发环境）：回退到原始直接函数调用管线

原有管线节点（intent_slot / tool_execution / response_generation）仍保留在
graph/nodes/ 中，作为回退路径。
"""
import asyncio
import hashlib
import json
import time
from typing import Any, Dict, List, AsyncIterator

from loguru import logger

from graph.state import TourismStateDict
from cache.query_cache import QueryCache


_response_cache: QueryCache = None


async def _get_response_cache() -> QueryCache:
    global _response_cache
    if _response_cache is None:
        _response_cache = QueryCache()
    return _response_cache


async def _cache_response(cache: QueryCache, cache_key: str, result: dict) -> None:
    """缓存对话响应，TTL=5分钟，Redis不可用时静默跳过。"""
    try:
        await cache.set("response", {"q": cache_key}, result, ttl=300)
    except Exception:
        pass  # Redis 不可用不影响主流程


def json_dumps_safe(obj):
    return json.dumps(obj, ensure_ascii=False, default=str)


async def run_agent(
    query: str,
    session_id: str = "default",
    user_id: int = None,
    history: List[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """运行旅游助手管线（非流式）—— 三级降级策略，含响应缓存。

    Tier 1: OrchestratorAgent（A2A + MCP 多智能体架构）
    Tier 2: LangGraph StateGraph 管线（基于 LangGraph 的状态机驱动工作流）
    Tier 3: 原始直接函数调用管线（最终回退）
    """
    # === 响应缓存：同一 query 5分钟内直接返回 ===
    cache = await _get_response_cache()
    cache_key = f"resp:{hashlib.md5(query.encode()).hexdigest()}"
    if history is None or len(history) == 0:
        cached = await cache.get("response", {"q": cache_key})
        if cached:
            logger.info(f"Cache HIT for query: {query[:50]}")
            return cached

    # === Tier 1: Orchestrator（A2A + MCP） ===
    from agents import get_orchestrator
    orchestrator = get_orchestrator()
    if orchestrator is not None:
        result = await orchestrator.process(
            query=query, session_id=session_id, user_id=user_id,
            history=history if history else None,
        )
        await _cache_response(cache, cache_key, result)
        return result

    # === Tier 2: LangGraph StateGraph 管线 ===
    from graph.langgraph_builder import run_langgraph_agent
    result = await run_langgraph_agent(
        query=query, session_id=session_id, user_id=user_id,
        history=history if history else None,
    )
    if result is not None:
        await _cache_response(cache, cache_key, result)
        return result

    # === Tier 3: 原始直接函数调用管线（最终回退） ===
    start_time = time.time()
    state = await _build_state_fallback(query, session_id, user_id, history)

    from graph.nodes.intent_slot import intent_slot_node
    from graph.nodes.intent_router import intent_router_node
    state = await intent_slot_node(state)
    if state.get("next_step") == "end":
        duration_ms = int((time.time() - start_time) * 1000)
        await _save_memory_fallback(state, user_id, session_id, query, start_time)
        return _format_response(state, duration_ms)

    # 新增：意图路由节点 —— 生成 worker_targets，决定单/多任务分发策略
    state = await intent_router_node(state)
    if state.get("next_step") == "end":
        duration_ms = int((time.time() - start_time) * 1000)
        await _save_memory_fallback(state, user_id, session_id, query, start_time)
        return _format_response(state, duration_ms)

    from graph.nodes.tool_execution import tool_execution_node

    sub_tasks = state.get("sub_tasks", [])
    if len(sub_tasks) > 1:
        state = await _execute_multi_tasks_fallback(state)
    else:
        state = await tool_execution_node(state)

    from graph.nodes.response_generation import final_answer_node
    state = await final_answer_node(state)

    duration_ms = int((time.time() - start_time) * 1000)
    await _save_memory_fallback(state, user_id, session_id, query, start_time)
    logger.info(f"Agent(fallback) [{session_id}]: complete in {duration_ms}ms")
    result = _format_response(state, duration_ms)
    await _cache_response(cache, cache_key, result)
    return result


async def run_agent_stream(
    query: str,
    session_id: str = "default",
    user_id: int = None,
    history: List[Dict[str, str]] = None,
) -> AsyncIterator[str]:
    """运行旅游助手管线（流式）—— 三级降级策略。

    Tier 1: OrchestratorAgent（A2A + MCP 多智能体架构）
    Tier 2: LangGraph StateGraph 管线
    Tier 3: 原始流式管线（最终回退）
    """
    # === Tier 1: Orchestrator（A2A + MCP） ===
    from agents import get_orchestrator
    orchestrator = get_orchestrator()
    if orchestrator is not None:
        async for event in orchestrator.process_stream(
            query=query, session_id=session_id, user_id=user_id,
            history=history if history else None,
        ):
            yield event
        return

    # === Tier 2: LangGraph StateGraph 管线 ===
    from graph.langgraph_builder import run_langgraph_agent_stream
    langgraph_stream = run_langgraph_agent_stream(
        query=query, session_id=session_id, user_id=user_id,
        history=history if history else None,
    )
    first_event = await langgraph_stream.__anext__()
    if first_event is not None:
        # LangGraph 可用，继续消费剩余事件
        yield first_event
        async for event in langgraph_stream:
            yield event
        return

    # === Tier 3: 原始流式管线（最终回退） ===
    start_time = time.time()

    def _sse(step: str) -> str:
        return f"data: {json_dumps_safe({'type': 'status', 'step': step})}\n\n"

    yield _sse("loading")
    state = await _build_state_fallback(query, session_id, user_id, history)

    yield _sse("analyzing")
    from graph.nodes.intent_slot import intent_slot_node
    from graph.nodes.intent_router import intent_router_node
    state = await intent_slot_node(state)
    if state.get("next_step") == "end":
        duration_ms = int((time.time() - start_time) * 1000)
        answer = state.get("final_answer", "")
        yield f"data: {json_dumps_safe({'type': 'answer', 'content': answer, 'intent': state.get('intent', ''), 'follow_up_needed': True, 'duration_ms': duration_ms})}\n\n"
        yield _sse("done")
        yield "data: [DONE]\n\n"
        await _save_memory_fallback(state, user_id, session_id, query, start_time)
        return

    # 新增：意图路由节点
    state = await intent_router_node(state)
    if state.get("next_step") == "end":
        duration_ms = int((time.time() - start_time) * 1000)
        answer = state.get("final_answer", "")
        yield f"data: {json_dumps_safe({'type': 'answer', 'content': answer, 'intent': state.get('intent', ''), 'follow_up_needed': True, 'duration_ms': duration_ms})}\n\n"
        yield _sse("done")
        yield "data: [DONE]\n\n"
        await _save_memory_fallback(state, user_id, session_id, query, start_time)
        return

    yield f"data: {json_dumps_safe({'type': 'intent', 'intent': state.get('intent', ''), 'slots': state.get('slots', {})})}\n\n"

    yield _sse("searching")
    from graph.nodes.tool_execution import tool_execution_node

    sub_tasks = state.get("sub_tasks", [])
    if len(sub_tasks) > 1:
        state = await _execute_multi_tasks_fallback(state)
    else:
        state = await tool_execution_node(state)
    yield f"data: {json_dumps_safe({'type': 'tool', 'tool_name': state.get('tool_name', ''), 'success': state.get('tool_result', {}).get('success', False)})}\n\n"

    yield _sse("generating")
    from graph.nodes.response_generation import final_answer_stream
    async for token in final_answer_stream(state):
        yield f"data: {json_dumps_safe({'type': 'token', 'content': token})}\n\n"

    duration_ms = int((time.time() - start_time) * 1000)
    yield f"data: {json_dumps_safe({'type': 'done', 'intent': state.get('intent', ''), 'duration_ms': duration_ms, 'follow_up_needed': False})}\n\n"
    yield _sse("done")
    yield "data: [DONE]\n\n"

    await _save_memory_fallback(state, user_id, session_id, query, start_time)
    logger.info(f"Agent(fallback stream) [{session_id}]: complete in {duration_ms}ms")


# ==================== 回退：原始辅助逻辑（直接从 builder.py 旧版保留） ====================


async def _execute_multi_tasks_fallback(state: TourismStateDict) -> TourismStateDict:
    """并行执行多个子任务，合并工具结果（回退版本）。"""
    from graph.nodes.tool_execution import tool_execution_node

    sub_tasks = state.get("sub_tasks", [])

    async def _run_one(task: dict) -> tuple:
        task_intent = task.get("intent", "")
        task_slots = task.get("slots", {})
        if not task_intent:
            return None, None, None
        temp_state = dict(state)
        temp_state["intent"] = task_intent
        temp_state["slots"] = task_slots
        temp_state = await tool_execution_node(temp_state)
        return (
            task_intent,
            temp_state.get("tool_name", ""),
            temp_state.get("tool_result", {}),
        )

    results = await asyncio.gather(*[_run_one(t) for t in sub_tasks])

    all_results = {}
    tool_names = set()
    intent_counts = {}
    for intent_name, tool_name, tool_result in results:
        if intent_name is None:
            continue
        tool_names.add(tool_name)
        cnt = intent_counts.get(intent_name, 0) + 1
        intent_counts[intent_name] = cnt
        key = intent_name if cnt == 1 else f"{intent_name}_{cnt}"
        if tool_result.get("success"):
            all_results[key] = tool_result.get("data", [])
        else:
            all_results[key] = {"error": tool_result.get("error", "查询失败")}

    state["tool_name"] = "+".join(sorted(tool_names))
    state["tool_result"] = {"success": True, "data": all_results, "is_multi": True}
    state["sub_tasks"] = [{"intent": k} for k in all_results.keys()]
    state["next_step"] = "result_summary"
    logger.info(f"Multi-task(fallback): executed {len(all_results)} tasks in parallel")
    return state


async def _build_state_fallback(query, session_id, user_id, history):
    """构建初始状态（回退版本 —— 直接调用 MemoryService）。"""
    from services.memory_service import memory_service

    short_term_coro = memory_service.get_short_term(session_id)
    summary_coro = memory_service.get_session_summary(session_id)
    prefs_coro = memory_service.get_preference_context(user_id) if user_id else None

    if prefs_coro:
        redis_history, session_summary, long_term_context = await asyncio.gather(
            short_term_coro, summary_coro, prefs_coro
        )
    else:
        redis_history, session_summary = await asyncio.gather(short_term_coro, summary_coro)
        long_term_context = ""

    if long_term_context:
        logger.info(f"Agent [{session_id}]: loaded long-term memory for user={user_id}")

    if history and not redis_history:
        merged_history = history
    elif history and redis_history:
        merged_history = redis_history
    else:
        merged_history = redis_history

    existing_slots = {}
    previous_intent = ""
    for msg in reversed(redis_history):
        if isinstance(msg, dict):
            if msg.get("slots"):
                for k, v in msg["slots"].items():
                    if v and v != "null" and k not in existing_slots:
                        existing_slots[k] = v
            if not previous_intent and msg.get("intent") and msg.get("role") == "user":
                previous_intent = msg["intent"]

    enriched_query = query
    context_parts = []
    if session_summary:
        context_parts.append(f"[对话摘要] {session_summary}")
    if long_term_context:
        context_parts.append(long_term_context)
    if context_parts:
        enriched_query = f"{query}\n" + "\n".join(context_parts)

    state: TourismStateDict = {
        "query": enriched_query,
        "original_query": query,
        "session_id": session_id,
        "history": merged_history,
        "intent": previous_intent,
        "intent_in_scope": True,
        "need_planning": False,
        "sub_tasks": [],
        "slots": existing_slots,
        "missing_slots": [],
        "follow_up_question": "",
        "tool_name": "",
        "tool_input": {},
        "tool_result": None,
        "summary": "",
        "final_answer": "",
        "follow_up_count": 0,
        "next_step": "intent_slot",
        "error": "",
    }
    logger.info(f"Agent [{session_id}]: starting (fallback), query='{query[:80]}'")
    return state


async def _save_memory_fallback(state, user_id, session_id, original_query, start_time):
    """保存记忆（回退版本 —— 直接调用 MemoryService）。"""
    from services.memory_service import memory_service
    from config import REDIS_CONFIG

    intent = state.get("intent", "")
    slots = state.get("slots", {})
    final_answer = state.get("final_answer", "")

    redis_tasks = [
        memory_service.add_short_term(session_id, "user", original_query, intent=intent, slots=slots)
    ]
    if final_answer:
        redis_tasks.append(
            memory_service.add_short_term(session_id, "assistant", final_answer, intent=intent)
        )
    if redis_tasks:
        results = await asyncio.gather(*redis_tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                logger.error(f"Failed to save Redis memory: {r}")

    if user_id:
        from celery_tasks.memory import (
            save_long_term_memory,
            save_conversation_history,
            extract_and_save_preferences,
        )
        tool_name = state.get("tool_name", "")
        result_summary = state.get("summary", "")
        duration_ms = int((time.time() - start_time) * 1000)
        success = bool(final_answer and "不在范围内" not in final_answer)

        save_long_term_memory.delay(
            user_id=user_id, session_id=session_id, query=original_query,
            intent=intent, slots=slots, tool_name=tool_name,
            result_summary=result_summary, final_answer=final_answer,
            duration_ms=duration_ms, success=success,
        )
        if slots:
            extract_and_save_preferences.delay(user_id, intent, slots)
        save_conversation_history.delay(
            user_id, session_id, "user", original_query, intent=intent, slots=slots,
        )
        if final_answer:
            save_conversation_history.delay(
                user_id, session_id, "assistant", final_answer, intent=intent,
            )

    if len(state.get("history", [])) > REDIS_CONFIG.get("max_history_turns", 10):
        from celery_tasks.memory import compress_single_session
        compress_single_session.delay(session_id)


def _format_response(state: TourismStateDict, duration_ms: int) -> Dict[str, Any]:
    missing = state.get("missing_slots", [])
    return {
        "session_id": state.get("session_id", "default"),
        "answer": state.get("final_answer", ""),
        "intent": state.get("intent", ""),
        "follow_up_needed": len(missing) > 0 if missing else False,
        "follow_up_question": state.get("follow_up_question", ""),
        "duration_ms": duration_ms,
    }
