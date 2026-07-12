"""上下文管理器 —— Token 估算、智能截断、增量状态更新。

优化说明 (P2):
  - 从 agent.py 拆出: _build_state 逻辑
  - 新增 estimate_tokens(): 估算当前上下文 token 数
  - 新增 truncate_context(): 超长时智能截断（保留最近轮次 + 摘要）
  - 新增 incremental_update(): 避免全量状态拷贝
"""
import asyncio
from typing import Any, Dict, List, Optional

from loguru import logger

from config import REDIS_CONFIG
from graph.state import TourismStateDict
from middleware.trace_id import get_trace_id


class ContextManager:
    """上下文管理器 —— 负责状态构建、Token 管理、内存优化。

    依赖注入: pubsub (用于 Memory Agent 通信)，由 OrchestratorAgent 传入。
    """

    def __init__(self, pubsub=None):
        self.pubsub = pubsub
        self.memory_agent_name = "memory"

    # ==================== 状态构建 ====================

    async def build_state(
        self,
        query: str,
        session_id: str,
        user_id: int,
        history: List[Dict],
    ) -> TourismStateDict:
        """构建初始管线状态 —— 并行加载短期记忆 + 长期记忆。

        优化: 通过 Memory Agent (MCP) 加载，带直接回退。
        """
        # 通过 Memory Agent 加载记忆
        load_tasks = [
            self._send_memory_request("get_short_term", {"session_id": session_id}),
            self._send_memory_request("get_session_summary", {"session_id": session_id}),
        ]
        if user_id:
            load_tasks.append(
                self._send_memory_request("get_preference_context", {"user_id": user_id})
            )

        results = await asyncio.gather(*load_tasks, return_exceptions=True)

        redis_history = []
        session_summary = ""
        long_term_context = ""

        if not isinstance(results[0], Exception):
            redis_history = results[0] if isinstance(results[0], list) else []
        if len(results) > 1 and not isinstance(results[1], Exception):
            session_summary = results[1].get("summary", "") if isinstance(results[1], dict) else ""
        if len(results) > 2 and not isinstance(results[2], Exception):
            long_term_context = results[2].get("context", "") if isinstance(results[2], dict) else ""

        if long_term_context:
            logger.info(f"ContextManager [{session_id}]: loaded long-term memory for user={user_id}")

        # 前端历史补充（低于 Redis 历史优先级）
        if history and not redis_history:
            merged_history = history
        elif history and redis_history:
            merged_history = redis_history
        else:
            merged_history = redis_history

        # 从 Redis 历史恢复上一轮槽位
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

        # 构造增强查询
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
            "follow_up_count": 0,
            "summary": "",
            "final_answer": "",
            "next_step": "intent_slot",
            "error": "",
        }
        logger.info(
            f"ContextManager [{session_id}]: starting, query='{query[:80]}', "
            f"prev_intent={previous_intent}, slots={existing_slots}"
        )
        return state

    # ==================== Token 管理 ====================

    @staticmethod
    def estimate_tokens(state: TourismStateDict) -> int:
        """估算当前状态的近似 token 数。

        中文字符数 × 1.5 近似为 token 数（适用于 Qwen 系列模型）。

        Returns:
            近似 token 数
        """
        total_chars = 0
        for key in ("query", "summary", "final_answer"):
            val = state.get(key, "")
            if isinstance(val, str):
                total_chars += len(val)
        # 历史消息
        for msg in state.get("history", []):
            if isinstance(msg, dict):
                total_chars += len(str(msg.get("content", "")))
        return int(total_chars * 1.5)

    @staticmethod
    def truncate_context(state: TourismStateDict, max_tokens: int = 4000) -> TourismStateDict:
        """超长上下文智能截断 —— 保留最近轮次 + 摘要。

        策略:
          1. 保留最近 10 轮对话
          2. 超出的部分用 state["summary"] 摘要替代
          3. 如果仍然超出，进一步缩短 query

        Args:
            state: 当前状态
            max_tokens: 最大允许 token 数

        Returns:
            截断后的状态（原地修改）
        """
        current_tokens = ContextManager.estimate_tokens(state)
        if current_tokens <= max_tokens:
            return state

        # 保留最近 10 轮
        history = state.get("history", [])
        max_turns = REDIS_CONFIG.get("max_history_turns", 10)
        if len(history) > max_turns * 2:
            state["history"] = history[:max_turns * 2]
            logger.info(f"ContextManager: truncated history from {len(history)} to {max_turns * 2}")

        # 再次检查
        current_tokens = ContextManager.estimate_tokens(state)
        if current_tokens > max_tokens:
            query = state.get("query", "")
            if len(query) > 500:
                state["query"] = query[:500] + "..."
                logger.info(f"ContextManager: truncated query from {len(query)} to 500 chars")

        return state

    @staticmethod
    def incremental_update(state: TourismStateDict, delta: Dict[str, Any]) -> TourismStateDict:
        """增量更新状态 —— 仅覆盖 delta 中非 None 的字段，避免全量拷贝。

        Args:
            state: 当前状态
            delta: 要更新的字段字典

        Returns:
            更新后的状态（原地修改）
        """
        for key, value in delta.items():
            if value is not None:
                state[key] = value  # type: ignore
        return state

    # ==================== Memory Agent 交互 ====================

    async def _send_memory_request(self, method: str, params: Dict[str, Any]) -> Any:
        """向 Memory Agent 发送 MCP 请求，带直接回退。"""
        if not self.pubsub:
            return await self._direct_memory_call(method, params)

        from agent_bus.message import AgentMessage

        try:
            msg = AgentMessage.create_task(
                sender="orchestrator",
                receiver=self.memory_agent_name,
                task_type=method,
                payload=params,
                trace_id=get_trace_id(),
            )
            from agent_bus.error_handler import ErrorHandler

            async def _send():
                return await self.pubsub.request_response(
                    channel=f"agent:{self.memory_agent_name}:inbox",
                    message=msg,
                    timeout=5.0,
                )

            result = await ErrorHandler.with_retry(_send, max_retries=1, backoff=0.5)
            return result.payload
        except Exception:
            return await self._direct_memory_call(method, params)

    async def _direct_memory_call(self, method: str, params: Dict[str, Any]) -> Any:
        """直接调用 MemoryService（Memory Agent 不可用时的回退）。"""
        from services.memory_service import memory_service

        handlers = {
            "get_short_term": lambda: memory_service.get_short_term(params["session_id"]),
            "get_session_summary": lambda: self._direct_get_summary(params),
            "get_preference_context": lambda: memory_service.get_preference_context(
                params["user_id"]
            ),
            "add_short_term": lambda: self._direct_add_short_term(params),
        }

        handler = handlers.get(method)
        if handler:
            return await handler()
        return None

    async def _direct_get_summary(self, params: Dict) -> Dict:
        from services.memory_service import memory_service
        summary = await memory_service.get_session_summary(params["session_id"])
        return {"summary": summary}

    async def _direct_add_short_term(self, params: Dict) -> Dict:
        from services.memory_service import memory_service
        await memory_service.add_short_term(
            session_id=params["session_id"],
            role=params["role"],
            content=params["content"],
            intent=params.get("intent", ""),
            slots=params.get("slots"),
        )
        return {"ok": True}
