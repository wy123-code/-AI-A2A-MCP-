"""航班 Worker 插件声明。"""
from agents.worker.flight.agent import FlightWorkerAgent
from agents.plugin import PluginManifest

PLUGIN_MANIFEST = PluginManifest(
    agent_class=FlightWorkerAgent,
    agent_name="worker.flight",
    intents=["flight_ticket"],
    priority=1,
    load_balancer_weight=1.0,
)
