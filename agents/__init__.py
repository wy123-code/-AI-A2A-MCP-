"""Agents 多智能体系统 —— 初始化、全局访问、优雅关闭。

优化说明 (P4):
  - initialize_agents 启动 CircuitBreaker + MessageQueue 并注入 Orchestrator
  - shutdown_agents 优雅关闭所有基础设施
  - 支持 PluginRegistry 自动发现（优先），fallback 到硬编码列表
"""
import asyncio
from typing import List, Optional

from loguru import logger

from agent_bus.pubsub import MCPPubSub
from agent_bus.registry import AgentRegistry
from agents.worker.base import BaseWorkerAgent

# 全局单例引用
_orchestrator = None
_memory_agent = None
_workers: List[BaseWorkerAgent] = []
_pubsub: Optional[MCPPubSub] = None
_registry: Optional[AgentRegistry] = None
_circuit_breaker = None
_message_queue = None


async def initialize_agents(pubsub: MCPPubSub, registry: AgentRegistry) -> None:
    """初始化整个多智能体系统：基础设施 → Memory → Workers → Orchestrator。"""
    global _orchestrator, _memory_agent, _workers, _pubsub, _registry
    global _circuit_breaker, _message_queue
    _pubsub = pubsub
    _registry = registry

    # 0. 启动基础设施：熔断器 + 消息队列
    from agent_bus.circuit_breaker import CircuitBreaker
    from agent_bus.message_queue import MessageQueue

    _circuit_breaker = CircuitBreaker()
    await _circuit_breaker.start()
    logger.info("MultiAgent: CircuitBreaker started")

    _message_queue = MessageQueue()
    await _message_queue.start()
    logger.info("MultiAgent: MessageQueue started")

    # 1. 启动 Memory Agent（其他 Agent 可能依赖记忆服务）
    from agents.memory.agent import MemoryAgent
    _memory_agent = MemoryAgent(pubsub, registry)
    await _memory_agent.start()
    logger.info("MultiAgent: MemoryAgent started")

    # 2. 启动所有 Worker Agent（并行启动，注入 message_queue 用于离线重放）
    worker_classes = _get_worker_classes()
    _workers = [cls(pubsub, registry) for cls in worker_classes]
    await asyncio.gather(*[w.start() for w in _workers])
    # 每个 Worker 启动后 drain 离线消息队列
    for w in _workers:
        try:
            drained = await _message_queue.drain_queue(w.name)
            if drained:
                logger.info(f"MultiAgent: drained {len(drained)} queued messages for {w.name}")
        except Exception as e:
            logger.warning(f"MultiAgent: drain for {w.name} failed: {e}")
    logger.info(f"MultiAgent: {len(_workers)} WorkerAgents started")

    # 3. 启动 Orchestrator Agent（注入熔断器和消息队列到调度器）
    from agents.orchestrator.agent import OrchestratorAgent
    _orchestrator = OrchestratorAgent(pubsub, registry)
    # 注入熔断器 + 消息队列到内部 TaskScheduler
    _orchestrator.scheduler.circuit_breaker = _circuit_breaker
    _orchestrator.scheduler.message_queue = _message_queue
    await _orchestrator.start()
    logger.info("MultiAgent: OrchestratorAgent started — system ready")


async def shutdown_agents() -> None:
    """优雅关闭所有 Agent 及基础设施。"""
    global _orchestrator, _memory_agent, _workers
    global _circuit_breaker, _message_queue
    if _orchestrator:
        await _orchestrator.stop()
    for w in _workers:
        await w.stop()
    if _memory_agent:
        await _memory_agent.stop()
    if _circuit_breaker:
        await _circuit_breaker.stop()
    if _message_queue:
        await _message_queue.stop()
    logger.info("MultiAgent: all agents and infrastructure stopped")


def get_orchestrator():
    """获取全局 Orchestrator Agent 实例。"""
    return _orchestrator


def get_memory_agent():
    """获取全局 Memory Agent 实例。"""
    return _memory_agent


def get_pubsub() -> Optional[MCPPubSub]:
    """获取全局 PubSub 实例。"""
    return _pubsub


def get_registry() -> Optional[AgentRegistry]:
    """获取全局 AgentRegistry 实例。"""
    return _registry


def get_circuit_breaker():
    """获取全局 CircuitBreaker 实例。"""
    return _circuit_breaker


def get_message_queue():
    """获取全局 MessageQueue 实例。"""
    return _message_queue


def _get_worker_classes():
    """返回所有 Worker Agent 类列表（按需导入，避免循环依赖）。

    优化 (P10): 优先使用 PluginRegistry 自动发现，失败则 fallback 到硬编码列表。
    """
    # 优先：PluginRegistry 自动发现
    try:
        from agents.plugin import PluginRegistry
        manifests = PluginRegistry.auto_discover()
        if manifests:
            return [m.agent_class for m in manifests]
    except Exception as e:
        logger.warning(f"PluginRegistry auto_discover failed: {e}, using fallback list")

    from agents.worker.weather import WeatherWorkerAgent
    from agents.worker.train_ticket import TrainTicketWorkerAgent
    from agents.worker.ticket import TicketWorkerAgent
    from agents.worker.attraction import AttractionWorkerAgent
    from agents.worker.hotel import HotelWorkerAgent
    from agents.worker.car_rental import CarRentalWorkerAgent
    from agents.worker.insurance import InsuranceWorkerAgent
    from agents.worker.tour_group import TourGroupWorkerAgent
    from agents.worker.flight import FlightWorkerAgent

    return [
        WeatherWorkerAgent,
        TrainTicketWorkerAgent,
        TicketWorkerAgent,
        AttractionWorkerAgent,
        HotelWorkerAgent,
        CarRentalWorkerAgent,
        InsuranceWorkerAgent,
        TourGroupWorkerAgent,
        FlightWorkerAgent,
    ]
