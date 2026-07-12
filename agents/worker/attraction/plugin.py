"""景点推荐 Worker 插件声明。"""
from agents.worker.attraction.agent import AttractionWorkerAgent
from agents.plugin import PluginManifest

PLUGIN_MANIFEST = PluginManifest(
    agent_class=AttractionWorkerAgent,
    agent_name="worker.attraction",
    intents=["attraction_recommend"],
    priority=7,
    load_balancer_weight=1.0,
)
