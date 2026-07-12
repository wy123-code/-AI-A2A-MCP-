"""保险查询 Worker Agent —— 封装 A2A SQL 查询保险数据能力。"""
from typing import Any, Dict

from agent_bus.pubsub import MCPPubSub
from agent_bus.registry import AgentRegistry
from agents.worker.base import BaseWorkerAgent


class InsuranceWorkerAgent(BaseWorkerAgent):
    """保险查询领域子智能体 —— LLM 生成 SQL → MCP 执行查询 MySQL insurance 表。"""

    supported_intents = ["insurance_query"]

    def __init__(self, pubsub: MCPPubSub, registry: AgentRegistry):
        super().__init__("worker.insurance", pubsub, registry)

    async def execute(
        self,
        intent: str,
        slots: Dict[str, Any],
        context: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        from .tool import insurance_tool
        return await insurance_tool(intent=intent, slots=slots)
