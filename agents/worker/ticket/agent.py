"""票务查询 Worker Agent —— 封装机票/船票/演唱会票查询能力。"""
from typing import Any, Dict

from agent_bus.pubsub import MCPPubSub
from agent_bus.registry import AgentRegistry
from agents.worker.base import BaseWorkerAgent


class TicketWorkerAgent(BaseWorkerAgent):
    """票务查询领域子智能体 —— LLM 生成模拟票务数据（机票/船票/演唱会票）。"""

    supported_intents = ["ship_ticket", "concert_ticket"]

    def __init__(self, pubsub: MCPPubSub, registry: AgentRegistry):
        super().__init__("worker.ticket", pubsub, registry)

    async def execute(
        self,
        intent: str,
        slots: Dict[str, Any],
        context: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        from .tool import query_ticket
        return await query_ticket(intent=intent, slots=slots)
