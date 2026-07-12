"""租车查询 Worker Agent —— 封装 A2A SQL 查询租车数据能力。"""
from typing import Any, Dict

from agent_bus.pubsub import MCPPubSub
from agent_bus.registry import AgentRegistry
from agents.worker.base import BaseWorkerAgent


class CarRentalWorkerAgent(BaseWorkerAgent):
    """租车查询领域子智能体 —— LLM 生成 SQL → MCP 执行查询 MySQL car_rental 表。"""

    supported_intents = ["car_rental_query"]

    def __init__(self, pubsub: MCPPubSub, registry: AgentRegistry):
        super().__init__("worker.car_rental", pubsub, registry)

    async def execute(
        self,
        intent: str,
        slots: Dict[str, Any],
        context: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        from .tool import car_rental_tool
        return await car_rental_tool(intent=intent, slots=slots)
