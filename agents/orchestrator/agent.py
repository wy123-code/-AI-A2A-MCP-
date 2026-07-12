"""编排调度主 Agent —— 承接用户请求，拆解任务，分发 Worker，聚合结果，生成回答。

架构改造 (P3):
  - 定位统一为全局调度路由 + 结果汇总整合中心，不介入具体业务执行
  - 新增 intent_router_node 调用，严格按流程图：请求→意图识别→路由分发→Worker执行→聚合→回答
  - 新增 Token 预算检查，超限自动截断上下文
  - 长期记忆保存统一走 MemoryAgent MCP 通信，消除跨层直接函数调用
"""
import asyncio
import json
import time
from typing import Any, AsyncIterator, Dict, List, Optional

from loguru import logger

from config import REDIS_CONFIG, AGENT_BUS_CONFIG
from agent_bus.message import AgentMessage
from agent_bus.pubsub import MCPPubSub
from agent_bus.registry import AgentRegistry
from middleware.trace_id import get_trace_id
from agents.worker.base import BaseAgent
from graph.state import TourismStateDict

from agents.orchestrator.scheduler import TaskScheduler
from agents.orchestrator.context_manager import ContextManager

# Token 预算配置（可后续移入 config.py）
MAX_CONTEXT_TOKENS = 4000
TOKEN_WARNING_THRESHOLD = 3000


def json_dumps_safe(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _sse_status(step: str) -> str:
    return f"data: {json_dumps_safe({'type': 'status', 'step': step})}\n\n"


class OrchestratorAgent(BaseAgent):
    """编排调度主 Agent —— 全局调度路由 + 结果汇总整合中心。

    严格按照流程图流程：
      用户请求 → 意图识别 → 意图路由 → 领域Worker执行 → 数据聚合 → 结果汇总 → 生成回答
    """

    def __init__(self, pubsub: MCPPubSub, registry: AgentRegistry):
        super().__init__("orchestrator", "orchestrator", pubsub, registry)
        self.memory_agent_name = "memory"
        self.scheduler = TaskScheduler(pubsub, registry)
        self.context_mgr = ContextManager(pubsub)

    async def handle_message(self, msg: AgentMessage) -> Optional[AgentMessage]:
        """Orchestrator 被动接收消息（当前不处理入站消息）。"""
        return None

    # ==================== 主入口 (非流式) ====================

    async def process(
        self,
        query: str,
        session_id: str = "default",
        user_id: int = None,
        history: List[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """运行完整非流式管线。"""
        start_time = time.time()
        trace_id = get_trace_id()

        # Step 0: 构建初始状态（加载记忆）
        state = await self.context_mgr.build_state(query, session_id, user_id, history)

        # Token 预算检查 —— 超限自动截断
        state = await self._check_token_budget(state)

        # Step 1: 意图识别 + 槽位填充
        state = await self._run_intent_slot(state)
        if state.get("next_step") == "end":
            duration_ms = int((time.time() - start_time) * 1000)
            await self._save_memory(state, user_id, session_id, query, start_time)
            return _format_response(state, duration_ms)

        # Step 2: 意图路由 → Worker 分发
        state = await self._route_and_dispatch(state, trace_id)

        # Step 3: 结果聚合 → 注入摘要提示词
        state = await self._aggregate_and_summarize(state)

        # Step 4: 最终回答生成
        from graph.nodes.response_generation import final_answer_node
        state = await final_answer_node(state)

        duration_ms = int((time.time() - start_time) * 1000)
        await self._save_memory(state, user_id, session_id, query, start_time)
        await self._record_metrics(state, duration_ms)
        logger.info(f"Orchestrator [{session_id}]: pipeline complete in {duration_ms}ms")
        return _format_response(state, duration_ms)

    # ==================== 主入口 (流式) ====================

    async def process_stream(
        self,
        query: str,
        session_id: str = "default",
        user_id: int = None,
        history: List[Dict[str, str]] = None,
    ) -> AsyncIterator[str]:
        """运行完整流式管线，逐个 yield SSE 事件。"""
        start_time = time.time()
        trace_id = get_trace_id()

        yield _sse_status("loading")
        state = await self.context_mgr.build_state(query, session_id, user_id, history)

        # Token 预算检查
        state = await self._check_token_budget(state)

        # Step 1: 意图识别 + 槽位填充
        yield _sse_status("analyzing")
        state = await self._run_intent_slot(state)
        if state.get("next_step") == "end":
            duration_ms = int((time.time() - start_time) * 1000)
            answer = state.get("final_answer", "")
            yield f"data: {json_dumps_safe({'type': 'answer', 'content': answer, 'intent': state.get('intent', ''), 'follow_up_needed': True, 'duration_ms': duration_ms})}\n\n"
            yield _sse_status("done")
            yield "data: [DONE]\n\n"
            await self._save_memory(state, user_id, session_id, query, start_time)
            return

        yield f"data: {json_dumps_safe({'type': 'intent', 'intent': state.get('intent', ''), 'slots': state.get('slots', {})})}\n\n"

        # Step 2: 意图路由 → Worker 分发
        yield _sse_status("searching")
        state = await self._route_and_dispatch(state, trace_id)
        yield f"data: {json_dumps_safe({'type': 'tool', 'tool_name': state.get('tool_name', ''), 'success': state.get('tool_result', {}).get('success', False)})}\n\n"

        # Step 3: 结果聚合
        state = await self._aggregate_and_summarize(state)

        # Step 4: 流式回答生成
        yield _sse_status("generating")
        from graph.nodes.response_generation import final_answer_stream
        async for token in final_answer_stream(state):
            yield f"data: {json_dumps_safe({'type': 'token', 'content': token})}\n\n"

        duration_ms = int((time.time() - start_time) * 1000)
        yield f"data: {json_dumps_safe({'type': 'done', 'intent': state.get('intent', ''), 'duration_ms': duration_ms, 'follow_up_needed': False})}\n\n"
        yield _sse_status("done")
        yield "data: [DONE]\n\n"

        await self._save_memory(state, user_id, session_id, query, start_time)
        await self._record_metrics(state, duration_ms)
        logger.info(f"Orchestrator(stream) [{session_id}]: complete in {duration_ms}ms")

    # ==================== 管线步骤 ====================

    async def _check_token_budget(self, state: TourismStateDict) -> TourismStateDict:
        """Token 预算检查 —— 超限自动截断上下文，节省 LLM 调用成本。"""
        tokens = self.context_mgr.estimate_tokens(state)
        if tokens > TOKEN_WARNING_THRESHOLD:
            logger.warning(
                f"Orchestrator: token budget warning ({tokens} > {TOKEN_WARNING_THRESHOLD}), truncating"
            )
            state = self.context_mgr.truncate_context(
                state, max_tokens=MAX_CONTEXT_TOKENS
            )
        return state

    async def _run_intent_slot(self, state: TourismStateDict) -> TourismStateDict:
        """调用意图识别 + 意图路由节点。"""
        from graph.nodes.intent_slot import intent_slot_node
        from graph.nodes.intent_router import intent_router_node

        state = await intent_slot_node(state)
        if state.get("next_step") == "end":
            return state

        # 新增：意图路由 —— 生成 worker_targets
        state = await intent_router_node(state)
        return state

    async def _route_and_dispatch(
        self, state: TourismStateDict, trace_id: str
    ) -> TourismStateDict:
        """统一路由分发：根据 worker_targets 决定单/多任务执行策略。"""
        worker_targets = state.get("worker_targets", [])
        sub_tasks = state.get("sub_tasks", [])

        if len(worker_targets) > 1 or len(sub_tasks) > 1:
            logger.info(f"Orchestrator: multi-task dispatch ({len(worker_targets)} targets)")
            state = await self.scheduler.execute_multi(state, trace_id)
        else:
            logger.info(f"Orchestrator: single-task dispatch")
            state = await self.scheduler.execute_single(state, trace_id)

        return state

    async def _aggregate_and_summarize(self, state: TourismStateDict) -> TourismStateDict:
        """结果聚合：去重 → 排序 → 生成摘要注入 final_answer 上下文。"""
        from agents.orchestrator.aggregator import aggregate_results

        tool_result = state.get("tool_result", {})
        if not tool_result or not tool_result.get("success"):
            return state

        # 单任务或多任务都走聚合流程（去重、排序）
        data = tool_result.get("data", {})
        if isinstance(data, dict) and tool_result.get("is_multi"):
            # 多任务：每项各自去重
            for key in data:
                if isinstance(data[key], list) and len(data[key]) > 1:
                    from agents.orchestrator.aggregator import resolve_conflicts
                    data[key] = resolve_conflicts(data[key], key_field="name")

        # 生成摘要片段
        summary_parts = []
        intents_processed = set()
        if state.get("sub_tasks"):
            for t in state["sub_tasks"]:
                intents_processed.add(t.get("intent", ""))
        if state.get("intent"):
            intents_processed.add(state["intent"])

        intent_count = len(intents_processed)
        result_count = 0
        if isinstance(data, dict):
            for v in data.values():
                if isinstance(v, list):
                    result_count += len(v)
        elif isinstance(data, list):
            result_count = len(data)

        if result_count > 0:
            summary_parts.append(
                f"共查询 {intent_count} 类信息，返回 {result_count} 条结果"
            )
        if tool_result.get("partial_failure"):
            summary_parts.append("部分查询暂时不可用")

        state["summary"] = "；".join(summary_parts) if summary_parts else ""
        return state

    async def _record_metrics(self, state: TourismStateDict, duration_ms: int) -> None:
        """记录全链路指标到 MetricsCollector。"""
        try:
            from common.monitor import metrics_collector
            intent = state.get("intent", "unknown")
            tool_result = state.get("tool_result", {})
            success = tool_result.get("success", False) if tool_result else bool(state.get("final_answer"))
            await metrics_collector.record_request(intent, duration_ms, success)
        except Exception:
            pass

    # ==================== Memory 交互 (统一走 MCP) ====================

    async def _send_memory_request(self, method: str, params: Dict[str, Any]) -> Any:
        """向 Memory Agent 发送 MCP 请求。"""
        return await self.context_mgr._send_memory_request(method, params)

    async def _save_memory(
        self, state: TourismStateDict, user_id: int, session_id: str,
        original_query: str, start_time: float,
    ) -> None:
        """通过 Memory Agent (MCP) 统一保存短期/长期记忆。

        所有记忆操作统一走 MCP 协议 → MemoryAgent，不再直接调用 Celery 或 MemoryService。
        """
        intent = state.get("intent", "")
        slots = state.get("slots", {})
        final_answer = state.get("final_answer", "")

        # 短期记忆保存（fire-and-forget via MCP）
        asyncio.create_task(self._send_memory_request(
            "add_short_term",
            {"session_id": session_id, "role": "user", "content": original_query,
             "intent": intent, "slots": slots},
        ))
        if final_answer:
            asyncio.create_task(self._send_memory_request(
                "add_short_term",
                {"session_id": session_id, "role": "assistant", "content": final_answer,
                 "intent": intent},
            ))

        # 长期记忆写入 —— 通过 Memory Agent (MCP) 统一转发 Celery 任务
        if user_id:
            tool_name = state.get("tool_name", "")
            result_summary = state.get("summary", "")
            duration_ms = int((time.time() - start_time) * 1000)
            success = bool(final_answer and "不在范围内" not in final_answer)

            asyncio.create_task(self._send_memory_request(
                "save_long_term",
                {
                    "user_id": user_id, "session_id": session_id,
                    "query": original_query, "intent": intent, "slots": slots,
                    "tool_name": tool_name, "result_summary": result_summary,
                    "final_answer": final_answer, "duration_ms": duration_ms,
                    "success": success,
                },
            ))

            if slots:
                asyncio.create_task(self._send_memory_request(
                    "extract_preferences",
                    {"user_id": user_id, "intent": intent, "slots": slots},
                ))

            asyncio.create_task(self._send_memory_request(
                "save_conversation",
                {
                    "user_id": user_id, "session_id": session_id,
                    "role": "user", "content": original_query,
                    "intent": intent, "slots": slots,
                },
            ))
            if final_answer:
                asyncio.create_task(self._send_memory_request(
                    "save_conversation",
                    {
                        "user_id": user_id, "session_id": session_id,
                        "role": "assistant", "content": final_answer,
                        "intent": intent,
                    },
                ))

        # 超长会话触发异步压缩
        if len(state.get("history", [])) > REDIS_CONFIG.get("max_history_turns", 10):
            asyncio.create_task(self._send_memory_request(
                "compress_session",
                {"session_id": session_id},
            ))


# ==================== 工具函数 ====================

def _format_response(state: TourismStateDict, duration_ms: int) -> Dict[str, Any]:
    """格式化 Agent 响应结果（对外 API 格式不变）。"""
    missing = state.get("missing_slots", [])
    return {
        "session_id": state.get("session_id", "default"),
        "answer": state.get("final_answer", ""),
        "intent": state.get("intent", ""),
        "follow_up_needed": len(missing) > 0 if missing else False,
        "follow_up_question": state.get("follow_up_question", ""),
        "duration_ms": duration_ms,
    }
