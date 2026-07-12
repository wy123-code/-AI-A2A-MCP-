"""景点推荐 Worker Agent —— 封装 Milvus 向量检索 + LLM 推荐能力。"""
from typing import Any, Dict

from agent_bus.pubsub import MCPPubSub
from agent_bus.registry import AgentRegistry
from agents.worker.base import BaseWorkerAgent


class AttractionWorkerAgent(BaseWorkerAgent):
    """景点推荐领域子智能体 —— Milvus 向量检索 + LLM 生成个性化景点推荐。"""

    supported_intents = ["attraction_recommend"]

    def __init__(self, pubsub: MCPPubSub, registry: AgentRegistry):
        super().__init__("worker.attraction", pubsub, registry)

    async def execute(
        self,
        intent: str,
        slots: Dict[str, Any],
        context: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        from .tool import recommend_attractions
        return await recommend_attractions(intent=intent, slots=slots)
