"""回答生成节点 —— 合并摘要与最终回答为一次 LLM 调用，支持流式输出。"""
import asyncio
import json
from datetime import date, datetime
from typing import AsyncIterator
from loguru import logger
from config import LLM_CONFIG, INTENT_CN_MAP
from llm.client_pool import llm_manager
from prompts import FINAL_ANSWER_PROMPT
from graph.state import TourismStateDict


def _build_intent_desc(state: TourismStateDict) -> str:
    """构建意图描述文本 —— 单任务直接返回意图中文名，多任务列出所有意图。"""
    intent = state.get("intent", "")
    sub_tasks = state.get("sub_tasks", [])
    if len(sub_tasks) <= 1:
        return INTENT_CN_MAP.get(intent, intent)
    names = []
    for i, t in enumerate(sub_tasks):
        name = t.get("intent", "")
        cn = INTENT_CN_MAP.get(name, name)
        names.append(f"{i+1}. {cn}")
    return "、".join(n for n in names)


def _json_serializer(obj):
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


async def final_answer_node(state: TourismStateDict) -> TourismStateDict:
    """合并摘要+最终回答，一次性生成（非流式，用于非流式端点）。"""
    query = state.get("original_query") or state.get("query", "")
    tool_result = state.get("tool_result", {})

    # 构造结果文本
    if not tool_result or not tool_result.get("success"):
        error_msg = tool_result.get("error", "查询服务暂时不可用")
        state["final_answer"] = f"抱歉，查询失败：{error_msg}。请稍后重试或尝试其他查询。"
        state["next_step"] = "end"
        return state

    result_data = tool_result.get("data", {})
    # 文本数据直接传递避免 JSON 转义干扰 LLM 解析，结构化数据才 JSON 序列化
    if isinstance(result_data, str):
        result_str = result_data
    else:
        result_str = json.dumps(result_data, ensure_ascii=False, indent=2, default=_json_serializer)

    intent_desc = _build_intent_desc(state)

    prompt = FINAL_ANSWER_PROMPT.format(
        query=query,
        intent=intent_desc,
        tool_result=result_str,
        today=date.today().strftime("%Y-%m-%d"),
    )

    logger.info(f"FinalAnswer: generating for intent={intent_desc}")
    client = llm_manager.get_client("default")

    try:
        response = await asyncio.to_thread(
            lambda: client.chat.completions.create(
                model=LLM_CONFIG["model"],
                messages=[{"role": "user", "content": prompt}],
                temperature=LLM_CONFIG["answer_temperature"],
                max_tokens=LLM_CONFIG["max_tokens"],
                timeout=30.0,
            )
        )
        final_answer = response.choices[0].message.content
    except Exception as e:
        logger.error(f"FinalAnswer LLM call failed: {e}")
        final_answer = result_str

    state["final_answer"] = final_answer
    state["next_step"] = "end"
    return state


async def final_answer_stream(state: TourismStateDict) -> AsyncIterator[str]:
    """流式生成最终回答，逐个 token yield 给调用方。"""
    query = state.get("original_query") or state.get("query", "")
    tool_result = state.get("tool_result", {})

    if not tool_result or not tool_result.get("success"):
        error_msg = tool_result.get("error", "查询服务暂时不可用")
        yield f"抱歉，查询失败：{error_msg}。请稍后重试。"
        return

    result_data = tool_result.get("data", {})
    if isinstance(result_data, str):
        result_str = result_data
    else:
        result_str = json.dumps(result_data, ensure_ascii=False, indent=2, default=_json_serializer)

    intent_desc = _build_intent_desc(state)

    prompt = FINAL_ANSWER_PROMPT.format(
        query=query,
        intent=intent_desc,
        tool_result=result_str,
        today=date.today().strftime("%Y-%m-%d"),
    )

    logger.info(f"FinalAnswer(stream): generating for intent={intent_desc}")

    queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def _generate():
        try:
            # 获取 LLM 客户端并创建流式请求
            client = llm_manager.get_client("default")
            stream = client.chat.completions.create(
                model=LLM_CONFIG["model"],
                messages=[{"role": "user", "content": prompt}],
                temperature=LLM_CONFIG["answer_temperature"],
                max_tokens=LLM_CONFIG["max_tokens"],
                stream=True,
                timeout=30.0,
            )
            # 逐 token 推送到异步队列
            for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    loop.call_soon_threadsafe(queue.put_nowait, ('token', delta.content))
        except Exception as e:
            loop.call_soon_threadsafe(queue.put_nowait, ('error', str(e)))
        loop.call_soon_threadsafe(queue.put_nowait, ('done', None))

    loop.run_in_executor(None, _generate)

    full_answer = ""
    while True:
        kind, value = await queue.get()
        if kind == 'done':
            break
        if kind == 'error':
            logger.error(f"FinalAnswer stream failed: {value}")
            state["final_answer"] = result_str
            state["next_step"] = "end"
            yield result_str
            return
        full_answer += value
        yield value

    state["final_answer"] = full_answer
    state["next_step"] = "end"
