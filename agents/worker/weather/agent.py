"""天气查询 Worker Agent —— 封装和风天气 API 查询能力。"""
from datetime import date
from typing import Any, Dict

from agent_bus.pubsub import MCPPubSub
from agent_bus.registry import AgentRegistry
from agents.worker.base import BaseWorkerAgent


class WeatherWorkerAgent(BaseWorkerAgent):
    """天气查询领域子智能体 —— 通过和风天气 API 获取实时天气 + 预报。"""

    supported_intents = ["weather_query"]

    def __init__(self, pubsub: MCPPubSub, registry: AgentRegistry):
        super().__init__("worker.weather", pubsub, registry)

    async def _preprocess(self, intent: str, slots: Dict[str, Any],
                          context: Dict[str, Any] = None) -> Dict[str, Any]:
        """槽位补全：若用户未指定日期，默认查询今天。"""
        if not slots.get("date"):
            slots["date"] = date.today().strftime("%Y-%m-%d")
        return slots

    async def execute(
        self,
        intent: str,
        slots: Dict[str, Any],
        context: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        from .tool import weather_tool
        return await weather_tool(intent=intent, slots=slots)
