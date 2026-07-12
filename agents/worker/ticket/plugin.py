"""票务 Worker 插件声明（船票 + 演唱会票）。"""
from agents.worker.ticket.agent import TicketWorkerAgent
from agents.plugin import PluginManifest

PLUGIN_MANIFEST = PluginManifest(
    agent_class=TicketWorkerAgent,
    agent_name="worker.ticket",
    intents=["ship_ticket", "concert_ticket"],
    priority=3,
    load_balancer_weight=1.0,
)
