"""LangGraph 状态图管线 —— 基于 LangGraph StateGraph 的多智能体协同工作流。

替代原有的手动 next_step 状态机，使用 LangGraph 提供的：
  - StateGraph 编译后的确定性状态流转
  - 条件边 (conditional_edges) 实现多分支路由
  - 编译后的图可序列化/可视化，便于调试

节点函数（intent_slot / intent_router / tool_execution / response_generation）
保持与原有实现完全一致，只是编排方式从手动顺序调用改为 LangGraph 状态图编译。
"""

import asyncio
import json
import time
from typing import Any, AsyncIterator, Dict, List

from loguru import logger

from graph.state import TourismStateDict


def json_dumps_safe(obj):
    return json.dumps(obj, ensure_ascii=False, default=str)


# ==================== LangGraph 状态图构建 ====================


def _build_tourism_graph():
    """构建 LangGraph StateGraph —— 旅游助手完整管线。

    图结构：
        [load_memory] → intent_slot → ┬─ next_step="end" ──────────→ END（追问/越界）
                                      └─ next_step="tool_execution" → intent_router
                                                                         │
                                      ┌─ next_step="end" ───────────────┘
                                      └─ next_step="dispatch_to_worker" → tool_execution
                                                                             │
                                                                             └→ response_generation → save_memory → END

    兼容性说明：
      - 如果 langgraph 未安装，导入时静默降级，run_langgraph_agent 返回 None
      - 调用方（builder.py）检测到 None 后自动回退到原始手动管线
    """
    try:
        from langgraph.graph import StateGraph, END
    except ImportError:
        logger.warning("LangGraph not installed — langgraph_builder will use fallback")
        return None

    workflow = StateGraph(TourismStateDict)

    # ---- 注册节点 ----
    # 节点1: 意图识别 & 槽位填充
    async def _intent_slot_node(state: TourismStateDict) -> TourismStateDict:
        from graph.nodes.intent_slot import intent_slot_node
        return await intent_slot_node(state)

    # 节点2: 意图路由 & Worker 分发规划
    async def _intent_router_node(state: TourismStateDict) -> TourismStateDict:
        from graph.nodes.intent_router import intent_router_node
        return await intent_router_node(state)

    # 节点3: 工具执行
    async def _tool_execution_node(state: TourismStateDict) -> TourismStateDict:
        from graph.nodes.tool_execution import tool_execution_node

        sub_tasks = state.get("sub_tasks", [])
        if len(sub_tasks) > 1:
            # 多任务并行执行
            return await _execute_multi_tasks(state)
        else:
            return await tool_execution_node(state)

    # 节点4: 最终回答生成
    async def _response_node(state: TourismStateDict) -> TourismStateDict:
        from graph.nodes.response_generation import final_answer_node
        return await final_answer_node(state)

    workflow.add_node("intent_slot", _intent_slot_node)
    workflow.add_node("intent_router", _intent_router_node)
    workflow.add_node("tool_execution", _tool_execution_node)
    workflow.add_node("response_generation", _response_node)

    # ---- 设置入口 ----
    workflow.set_entry_point("intent_slot")

    # ---- 条件边：intent_slot 之后的分流 ----
    def _after_intent_slot(state: TourismStateDict) -> str:
        """根据 next_step 决定下一步走向。"""
        return state.get("next_step", "end")

    workflow.add_conditional_edges(
        "intent_slot",
        _after_intent_slot,
        {
            "end": END,
            "tool_execution": "intent_router",
        },
    )

    # ---- 条件边：intent_router 之后的分流 ----
    def _after_intent_router(state: TourismStateDict) -> str:
        """根据 next_step 决定下一步走向。"""
        return state.get("next_step", "end")

    workflow.add_conditional_edges(
        "intent_router",
        _after_intent_router,
        {
            "end": END,
            "dispatch_to_worker": "tool_execution",
        },
    )

    # ---- 固定边 ----
    workflow.add_edge("tool_execution", "response_generation")
    workflow.add_edge("response_generation", END)

    # ---- 编译 ----
    compiled = workflow.compile()
    logger.info("LangGraph pipeline compiled successfully (StateGraph with 4 nodes)")
    return compiled


# 模块级单例：首次使用时编译，后续复用
_graph = None


def _get_graph():
    """懒加载获取编译后的 LangGraph 状态图。"""
    global _graph
    if _graph is None:
        _graph = _build_tourism_graph()
    return _graph


# ==================== 公共入口（兼容原有接口） ====================


async def run_langgraph_agent(
    query: str,
    session_id: str = "default",
    user_id: int = None,
    history: List[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """通过 LangGraph 状态图运行旅游助手管线（非流式）。

    如果 LangGraph 不可用，返回 None 让调用方回退到原始管线。
    """
    compiled_graph = _get_graph()
    if compiled_graph is None:
        return None  # 信号：LangGraph 不可用，调用方应回退

    start_time = time.time()

    # 构建初始状态（与原 builder.py 中 _build_state_fallback 一致）
    state = await _build_initial_state(query, session_id, user_id, history)

    # 通过 LangGraph 执行完整管线
    try:
        final_state = await compiled_graph.ainvoke(state)
    except Exception as e:
        logger.error(f"LangGraph pipeline failed: {e}", exc_info=True)
        # 异常时返回 None，让调用方回退
        return None

    duration_ms = int((time.time() - start_time) * 1000)

    # 保存记忆
    await _save_memory(final_state, user_id, session_id, query, start_time)

    logger.info(f"Agent(LangGraph) [{session_id}]: complete in {duration_ms}ms")
    return _format_response(final_state, duration_ms)


async def run_langgraph_agent_stream(
    query: str,
    session_id: str = "default",
    user_id: int = None,
    history: List[Dict[str, str]] = None,
) -> AsyncIterator[str]:
    """通过 LangGraph 状态图运行旅游助手管线（流式）。

    流式输出在 response_generation 节点中通过 final_answer_stream 实现。
    由于 LangGraph 的 ainvoke 不支持中间 yield，这里采用分步执行：
      1. intent_slot → intent_router → tool_execution（通过 graph 子图）
      2. response_generation 使用流式输出

    如果 LangGraph 不可用，yield None 让调用方回退到原始管线。
    """
    compiled_graph = _get_graph()
    if compiled_graph is None:
        yield None  # 信号：LangGraph 不可用
        return

    start_time = time.time()

    def _sse(step: str) -> str:
        return f"data: {json_dumps_safe({'type': 'status', 'step': step})}\n\n"

    yield _sse("loading")
    state = await _build_initial_state(query, session_id, user_id, history)

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
        await _save_memory(state, user_id, session_id, query, start_time)
        return

    state = await intent_router_node(state)
    if state.get("next_step") == "end":
        duration_ms = int((time.time() - start_time) * 1000)
        answer = state.get("final_answer", "")
        yield f"data: {json_dumps_safe({'type': 'answer', 'content': answer, 'intent': state.get('intent', ''), 'follow_up_needed': True, 'duration_ms': duration_ms})}\n\n"
        yield _sse("done")
        yield "data: [DONE]\n\n"
        await _save_memory(state, user_id, session_id, query, start_time)
        return

    yield f"data: {json_dumps_safe({'type': 'intent', 'intent': state.get('intent', ''), 'slots': state.get('slots', {})})}\n\n"

    yield _sse("searching")
    from graph.nodes.tool_execution import tool_execution_node

    sub_tasks = state.get("sub_tasks", [])
    if len(sub_tasks) > 1:
        state = await _execute_multi_tasks(state)
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

    await _save_memory(state, user_id, session_id, query, start_time)
    logger.info(f"Agent(LangGraph stream) [{session_id}]: complete in {duration_ms}ms")


# ==================== 内部辅助函数 ====================


async def _build_initial_state(query, session_id, user_id, history):
    """构建初始管线状态 —— 从 graph/builder.py 的 _build_state_fallback 移植。"""
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
        logger.info(f"Agent(LangGraph) [{session_id}]: loaded long-term memory for user={user_id}")

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
    logger.info(f"Agent(LangGraph) [{session_id}]: starting, query='{query[:80]}'")
    return state


async def _execute_multi_tasks(state: TourismStateDict) -> TourismStateDict:
    """并行执行多个子任务，合并工具结果。"""
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
    logger.info(f"Multi-task(LangGraph): executed {len(all_results)} tasks in parallel")
    return state


async def _save_memory(state, user_id, session_id, original_query, start_time):
    """保存记忆 —— 从 graph/builder.py _save_memory_fallback 移植。"""
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
