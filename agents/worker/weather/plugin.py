"""天气 Worker 插件声明。"""
from agents.worker.weather.agent import WeatherWorkerAgent
from agents.plugin import PluginManifest

PLUGIN_MANIFEST = PluginManifest(
    agent_class=WeatherWorkerAgent,
    agent_name="worker.weather",
    intents=["weather_query"],
    priority=4,
    load_balancer_weight=1.0,
)
