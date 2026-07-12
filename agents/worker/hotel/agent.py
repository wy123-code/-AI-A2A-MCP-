"""酒店查询 Worker Agent —— 封装 A2A SQL 查询酒店数据能力。"""
from typing import Any, Dict

from agent_bus.pubsub import MCPPubSub
from agent_bus.registry import AgentRegistry
from agents.worker.base import BaseWorkerAgent


class HotelWorkerAgent(BaseWorkerAgent):
    """酒店查询领域子智能体 —— LLM 生成 SQL → MCP 执行查询 MySQL hotel 表。"""

    supported_intents = ["hotel_query"]

    def __init__(self, pubsub: MCPPubSub, registry: AgentRegistry):
        super().__init__("worker.hotel", pubsub, registry)

    async def execute(
        self,
        intent: str,
        slots: Dict[str, Any],
        context: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        from .tool import hotel_tool
        return await hotel_tool(intent=intent, slots=slots)
