"""旅行团查询 Worker Agent —— 封装 A2A SQL 查询旅行团数据能力。"""
from typing import Any, Dict

from agent_bus.pubsub import MCPPubSub
from agent_bus.registry import AgentRegistry
from agents.worker.base import BaseWorkerAgent


class TourGroupWorkerAgent(BaseWorkerAgent):
    """旅行团查询领域子智能体 —— LLM 生成 SQL → MCP 执行查询 MySQL tour_group 表。"""

    supported_intents = ["tour_group_query"]

    def __init__(self, pubsub: MCPPubSub, registry: AgentRegistry):
        super().__init__("worker.tour_group", pubsub, registry)

    async def execute(
        self,
        intent: str,
        slots: Dict[str, Any],
        context: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        from .tool import tour_group_tool
        return await tour_group_tool(intent=intent, slots=slots)
