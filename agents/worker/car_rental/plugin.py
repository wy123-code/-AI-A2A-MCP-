"""租车 Worker 插件声明。"""
from agents.worker.car_rental.agent import CarRentalWorkerAgent
from agents.plugin import PluginManifest

PLUGIN_MANIFEST = PluginManifest(
    agent_class=CarRentalWorkerAgent,
    agent_name="worker.car_rental",
    intents=["car_rental_query"],
    priority=9,
    load_balancer_weight=1.0,
)
