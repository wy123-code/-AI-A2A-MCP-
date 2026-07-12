"""航班查询 Worker Agent —— 封装 A2A SQL 查询航班数据能力。"""
from typing import Any, Dict

from agent_bus.pubsub import MCPPubSub
from agent_bus.registry import AgentRegistry
from agents.worker.base import BaseWorkerAgent


class FlightWorkerAgent(BaseWorkerAgent):
    """航班查询领域子智能体 —— LLM 生成 SQL → MCP 执行查询 MySQL flight 表。"""

    supported_intents = ["flight_ticket"]

    def __init__(self, pubsub: MCPPubSub, registry: AgentRegistry):
        super().__init__("worker.flight", pubsub, registry)

    async def execute(
        self,
        intent: str,
        slots: Dict[str, Any],
        context: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        from .tool import flight_tool
        return await flight_tool(intent=intent, slots=slots)
