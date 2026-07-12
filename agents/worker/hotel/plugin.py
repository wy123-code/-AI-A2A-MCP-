"""酒店 Worker 插件声明。"""
from agents.worker.hotel.agent import HotelWorkerAgent
from agents.plugin import PluginManifest

PLUGIN_MANIFEST = PluginManifest(
    agent_class=HotelWorkerAgent,
    agent_name="worker.hotel",
    intents=["hotel_query"],
    priority=5,
    load_balancer_weight=1.0,
)
