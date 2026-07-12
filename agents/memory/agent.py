"""全局记忆中心 Agent —— 统一管理会话记忆、用户偏好、对话历史。

优化 (P3): 新增 save_long_term / extract_preferences / compress_session 处理器，
所有长期记忆操作统一走 MCP 协议 → MemoryAgent → Celery 任务，消除跨层直接调用。
"""
from typing import Any, Dict, Optional

from loguru import logger

from agent_bus.message import AgentMessage
from agent_bus.pubsub import MCPPubSub
from agent_bus.registry import AgentRegistry
from agents.worker.base import BaseAgent


class MemoryAgent(BaseAgent):
    """全局记忆中心智能体 —— 所有 Agent 通过 MCP 统一读写记忆。

    封装 MemoryService，提供：
    - 短期记忆（Redis 会话上下文）
    - 长期记忆（MySQL 用户偏好/查询历史，通过 Celery 异步写入）
    - 会话摘要与压缩
    """

    def __init__(self, pubsub: MCPPubSub, registry: AgentRegistry):
        super().__init__("memory", "memory", pubsub, registry)

    async def handle_message(self, msg: AgentMessage) -> Optional[AgentMessage]:
        """路由方法调用到 MemoryService 或 Celery 任务。"""
        method = msg.task_type
        params = msg.payload

        handlers = {
            "get_short_term": self._handle_get_short_term,
            "add_short_term": self._handle_add_short_term,
            "get_session_summary": self._handle_get_session_summary,
            "get_preference_context": self._handle_get_preference_context,
            "get_preferences": self._handle_get_preferences,
            "save_conversation": self._handle_save_conversation,
            "save_query_log": self._handle_save_query_log,
            "clear_short_term": self._handle_clear_short_term,
            # 新增：长期记忆 + 偏好提取 + 会话压缩（转发 Celery）
            "save_long_term": self._handle_save_long_term,
            "extract_preferences": self._handle_extract_preferences,
            "compress_session": self._handle_compress_session,
        }

        handler = handlers.get(method)
        if not handler:
            return AgentMessage.create_result(
                original_msg=msg,
                status="failed",
                payload={"error": f"Unknown method: {method}"},
            )

        try:
            result = await handler(params)
            return AgentMessage.create_result(
                original_msg=msg,
                status="success",
                payload=result,
            )
        except Exception as e:
            logger.error(f"MemoryAgent: {method} failed: {e}")
            return AgentMessage.create_result(
                original_msg=msg,
                status="failed",
                payload={"error": str(e)},
            )

    # ==================== 短期记忆 ====================

    async def _handle_get_short_term(self, params: Dict) -> Any:
        from services.memory_service import memory_service
        return await memory_service.get_short_term(params["session_id"])

    async def _handle_add_short_term(self, params: Dict) -> Dict:
        from services.memory_service import memory_service
        await memory_service.add_short_term(
            session_id=params["session_id"],
            role=params["role"],
            content=params["content"],
            intent=params.get("intent", ""),
            slots=params.get("slots"),
        )
        return {"ok": True}

    async def _handle_get_session_summary(self, params: Dict) -> Dict:
        from services.memory_service import memory_service
        summary = await memory_service.get_session_summary(params["session_id"])
        return {"summary": summary}

    async def _handle_clear_short_term(self, params: Dict) -> Dict:
        from services.memory_service import memory_service
        await memory_service.clear_short_term(params["session_id"])
        return {"ok": True}

    # ==================== 长期记忆 ====================

    async def _handle_get_preference_context(self, params: Dict) -> Dict:
        from services.memory_service import memory_service
        ctx = await memory_service.get_preference_context(params["user_id"])
        return {"context": ctx}

    async def _handle_get_preferences(self, params: Dict) -> Any:
        from services.memory_service import memory_service
        return await memory_service.get_preferences(
            params["user_id"], category=params.get("category")
        )

    async def _handle_save_conversation(self, params: Dict) -> Dict:
        from services.memory_service import memory_service
        await memory_service.save_conversation(
            user_id=params["user_id"],
            session_id=params["session_id"],
            role=params["role"],
            content=params["content"],
            intent=params.get("intent", ""),
            slots=params.get("slots"),
        )
        return {"ok": True}

    async def _handle_save_query_log(self, params: Dict) -> Dict:
        from services.memory_service import memory_service
        await memory_service.save_query_log(
            user_id=params["user_id"],
            session_id=params["session_id"],
            query=params["query"],
            intent=params["intent"],
            slots=params.get("slots", {}),
            tool_name=params.get("tool_name", ""),
            result_summary=params.get("result_summary", ""),
            final_answer=params.get("final_answer", ""),
            duration_ms=params.get("duration_ms", 0),
            success=params.get("success", True),
        )
        return {"ok": True}

    # ==================== Celery 异步任务转发 ====================

    async def _handle_save_long_term(self, params: Dict) -> Dict:
        """通过 Celery 异步写入长期记忆（查询日志 + 会话历史）。"""
        from celery_tasks.memory import save_long_term_memory
        save_long_term_memory.delay(
            user_id=params["user_id"],
            session_id=params["session_id"],
            query=params["query"],
            intent=params["intent"],
            slots=params.get("slots", {}),
            tool_name=params.get("tool_name", ""),
            result_summary=params.get("result_summary", ""),
            final_answer=params.get("final_answer", ""),
            duration_ms=params.get("duration_ms", 0),
            success=params.get("success", True),
        )
        return {"ok": True, "queued": "save_long_term_memory"}

    async def _handle_extract_preferences(self, params: Dict) -> Dict:
        """通过 Celery 异步提取用户偏好。"""
        from celery_tasks.memory import extract_and_save_preferences
        extract_and_save_preferences.delay(
            params["user_id"], params["intent"], params["slots"]
        )
        return {"ok": True, "queued": "extract_and_save_preferences"}

    async def _handle_compress_session(self, params: Dict) -> Dict:
        """通过 Celery 异步压缩超长会话。"""
        from celery_tasks.memory import compress_single_session
        compress_single_session.delay(params["session_id"])
        return {"ok": True, "queued": "compress_single_session"}
