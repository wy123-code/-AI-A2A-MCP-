"""火车票 Worker 插件声明。"""
from agents.worker.train_ticket.agent import TrainTicketWorkerAgent
from agents.plugin import PluginManifest

PLUGIN_MANIFEST = PluginManifest(
    agent_class=TrainTicketWorkerAgent,
    agent_name="worker.train_ticket",
    intents=["train_ticket"],
    priority=2,
    load_balancer_weight=1.0,
)
