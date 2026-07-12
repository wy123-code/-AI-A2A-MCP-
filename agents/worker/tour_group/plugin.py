"""旅行团 Worker 插件声明。"""
from agents.worker.tour_group.agent import TourGroupWorkerAgent
from agents.plugin import PluginManifest

PLUGIN_MANIFEST = PluginManifest(
    agent_class=TourGroupWorkerAgent,
    agent_name="worker.tour_group",
    intents=["tour_group_query"],
    priority=6,
    load_balancer_weight=1.0,
)
