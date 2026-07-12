"""保险 Worker 插件声明。"""
from agents.worker.insurance.agent import InsuranceWorkerAgent
from agents.plugin import PluginManifest

PLUGIN_MANIFEST = PluginManifest(
    agent_class=InsuranceWorkerAgent,
    agent_name="worker.insurance",
    intents=["insurance_query"],
    priority=10,
    load_balancer_weight=1.0,
)
