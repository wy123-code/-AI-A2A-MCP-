"""火车票查询 Worker Agent —— 封装 12306 MCP 火车票查询能力。"""
from typing import Any, Dict

from agent_bus.pubsub import MCPPubSub
from agent_bus.registry import AgentRegistry
from agents.worker.base import BaseWorkerAgent


class TrainTicketWorkerAgent(BaseWorkerAgent):
    """火车票查询领域子智能体 —— 通过 12306 MCP 服务查询真实火车票信息。"""

    supported_intents = ["train_ticket"]

    def __init__(self, pubsub: MCPPubSub, registry: AgentRegistry):
        super().__init__("worker.train_ticket", pubsub, registry)

    async def execute(
        self,
        intent: str,
        slots: Dict[str, Any],
        context: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        from .tool import query_train_ticket
        return await query_train_ticket(intent=intent, slots=slots)
