"""任务调度器 —— DAG 依赖编排、熔断保护、健康预检、分层并行分发。

优化说明 (P4):
  - 集成 CircuitBreaker：分发前熔断检查，分发后状态更新
  - 集成 MessageQueue：Worker 离线时消息入持久队列
  - execute_multi 支持 DAG 分层执行（同层并行，跨层串行）
  - 故障容错：局部失败不中断整体流程，追踪 success/failure 计数
"""
import asyncio
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from config import AGENT_BUS_CONFIG
from agent_bus.message import AgentMessage
from mcp.enhanced_message import EnhancedAgentMessage
from agent_bus.pubsub import MCPPubSub
from agent_bus.registry import AgentRegistry
from agent_bus.error_handler import ErrorHandler
from graph.state import TourismStateDict
from agents.worker import get_worker_name


class TaskDependency:
    """任务依赖描述 —— 用于声明子任务间的执行顺序。"""

    def __init__(self, task_id: str, intent: str, slots: Dict[str, Any],
                 depends_on: List[str] = None, priority: int = 0):
        self.task_id = task_id
        self.intent = intent
        self.slots = slots
        self.depends_on = depends_on or []
        self.priority = priority


class TaskScheduler:
    """任务调度器 —— Worker 发现、熔断检查、健康预检、分层分发。

    依赖注入: pubsub, registry, circuit_breaker, message_queue (可选)。
    """

    def __init__(self, pubsub: MCPPubSub, registry: AgentRegistry,
                 circuit_breaker=None, message_queue=None):
        self.pubsub = pubsub
        self.registry = registry
        self.circuit_breaker = circuit_breaker
        self.message_queue = message_queue

    @staticmethod
    def resolve_order(tasks: List[TaskDependency]) -> List[List[TaskDependency]]:
        """拓扑排序 → 分层并行执行计划。"""
        if not tasks:
            return []

        task_map = {t.task_id: t for t in tasks}
        in_degree = {t.task_id: len(t.depends_on) for t in tasks}
        dependents: Dict[str, List[str]] = {t.task_id: [] for t in tasks}
        for t in tasks:
            for dep_id in t.depends_on:
                if dep_id in dependents:
                    dependents[dep_id].append(t.task_id)

        layers = []
        current = [t for t in tasks if in_degree[t.task_id] == 0]
        while current:
            current.sort(key=lambda t: t.priority)
            layers.append(current)
            next_layer = []
            for t in current:
                for dep_id in dependents.get(t.task_id, []):
                    in_degree[dep_id] -= 1
                    if in_degree[dep_id] == 0:
                        next_layer.append(task_map[dep_id])
            current = next_layer

        return layers

    # ==================== 单任务分发 ====================

    async def execute_single(
        self, state: TourismStateDict, trace_id: str = ""
    ) -> TourismStateDict:
        """通过 MCP 分发单个任务到对应 Worker Agent（含熔断+健康预检）。"""
        intent = state.get("intent", "")
        slots = dict(state.get("slots", {}))
        slots["_query"] = state.get("query", "")

        worker_name = await self._resolve_worker(intent)

        # 熔断检查
        if not await self._check_circuit(worker_name):
            state["tool_name"] = worker_name
            state["tool_result"] = ErrorHandler.degrade_factory(intent)
            state["next_step"] = "result_summary"
            return state

        # 健康预检
        if not await self._pre_check(worker_name, intent):
            state["tool_name"] = worker_name
            state["tool_result"] = ErrorHandler.degrade_factory(intent)
            state["next_step"] = "result_summary"
            return state

        msg = EnhancedAgentMessage.create_task_with_trace(
            sender="orchestrator",
            receiver=worker_name,
            task_type=intent,
            payload={
                "intent": intent,
                "slots": slots,
                "query": state.get("original_query", ""),
            },
            trace_id=trace_id,
            ttl=int(AGENT_BUS_CONFIG["default_task_timeout"]),
        )

        t_start = time.time()
        try:
            result_msg = await self._do_request(worker_name, msg)
            tool_result = result_msg.payload
            await self._after_call(worker_name, success=tool_result.get("success", False))
            await self._record_worker_metrics(worker_name, t_start, tool_result.get("success", False))
        except Exception as e:
            logger.error(f"Scheduler: worker '{worker_name}' failed for '{intent}': {e}")
            tool_result = ErrorHandler.degrade_factory(intent)
            tool_result["error"] = f"{tool_result['error']} (error: {e})"
            await self._after_call(worker_name, success=False)
            await self._record_worker_metrics(worker_name, t_start, False)

        state["tool_name"] = worker_name
        state["tool_result"] = tool_result
        state["next_step"] = "result_summary"
        return state

    # ==================== 多任务分发 (DAG 分层) ====================

    async def execute_multi(
        self, state: TourismStateDict, trace_id: str = ""
    ) -> TourismStateDict:
        """通过 MCP 分层并行分发多个子任务。

        支持 DAG 依赖编排：同层内并行执行，跨层串行等待。
        单层内按 INTENT_PRIORITY 优先级排序。
        """
        sub_tasks = state.get("sub_tasks", [])

        # 构建 TaskDependency 列表（从 sub_tasks 提取依赖与优先级）
        dependencies = []
        for i, task in enumerate(sub_tasks):
            intent = task.get("intent", "")
            if not intent:
                continue
            from config import INTENT_PRIORITY
            priority = INTENT_PRIORITY.get(intent, 5)
            deps = task.get("depends_on", [])
            deps = deps if isinstance(deps, list) else []
            dependencies.append(TaskDependency(
                task_id=task.get("task_id", f"task_{i}"),
                intent=intent,
                slots=task.get("slots", {}),
                depends_on=deps,
                priority=priority,
            ))

        if not dependencies:
            state["tool_result"] = {"success": False, "error": "No valid tasks", "data": {}}
            state["next_step"] = "result_summary"
            return state

        # DAG 分层
        layers = self.resolve_order(dependencies)
        logger.info(
            f"Scheduler: DAG resolved → {len(layers)} layers, "
            f"{sum(len(l) for l in layers)} tasks"
        )

        async def _dispatch_one(task: TaskDependency) -> Tuple:
            intent = task.intent
            slots = task.slots
            worker_name = await self._resolve_worker(intent)

            # 熔断检查
            if not await self._check_circuit(worker_name):
                return (intent, worker_name, ErrorHandler.degrade_factory(intent))

            # 健康预检 → 离线入持久队列
            if not await self._pre_check(worker_name, intent):
                await self._enqueue_offline(worker_name, intent, slots, trace_id)
                return (intent, worker_name, ErrorHandler.degrade_factory(intent))

            msg = EnhancedAgentMessage.create_task_with_trace(
                sender="orchestrator",
                receiver=worker_name,
                task_type=intent,
                payload={"intent": intent, "slots": slots},
                trace_id=trace_id,
                ttl=int(AGENT_BUS_CONFIG["default_task_timeout"]),
            )

            t_start = time.time()
            try:
                result_msg = await self._do_request(worker_name, msg)
                result = result_msg.payload
                await self._after_call(worker_name, success=result.get("success", False))
                await self._record_worker_metrics(worker_name, t_start, result.get("success", False))
                return (intent, worker_name, result)
            except Exception as e:
                logger.error(f"Scheduler: DAG dispatch failed for '{intent}': {e}")
                await self._after_call(worker_name, success=False)
                await self._record_worker_metrics(worker_name, t_start, False)
                return (intent, worker_name, {"success": False, "error": str(e), "data": []})

        # 分层执行：层内并行 (asyncio.gather)，层间串行
        all_results = []
        for layer_idx, layer in enumerate(layers):
            logger.info(f"Scheduler: executing layer {layer_idx} ({len(layer)} tasks in parallel)")
            layer_results = await asyncio.gather(*[_dispatch_one(t) for t in layer])
            all_results.extend(layer_results)

        # 聚合结果
        from agents.orchestrator.aggregator import aggregate_results
        state = aggregate_results(state, all_results)
        return state

    # ========== 内部方法 ==========

    async def _resolve_worker(self, intent: str) -> str:
        """根据意图查找目标 Worker 名称（优先注册中心动态发现）。"""
        worker_name = await self.registry.discover_by_intent(intent)
        return worker_name or get_worker_name(intent)

    async def _check_circuit(self, worker_name: str) -> bool:
        """熔断检查 —— 已熔断返回 False。"""
        if self.circuit_breaker:
            return await self.circuit_breaker.before_call(worker_name)
        return True

    async def _after_call(self, worker_name: str, success: bool) -> None:
        """调用后更新熔断状态。"""
        if self.circuit_breaker:
            await self.circuit_breaker.after_call(worker_name, success)

    async def _pre_check(self, worker_name: str, intent: str) -> bool:
        """调度前健康预检：Worker 在线返回 True，离线返回 False。"""
        if worker_name and not await self.registry.is_online(worker_name):
            logger.warning(
                f"Scheduler: worker '{worker_name}' is offline, "
                f"degrading for intent={intent}"
            )
            return False
        return True

    async def _enqueue_offline(self, worker_name: str, intent: str,
                               slots: dict, trace_id: str) -> None:
        """Worker 离线时将任务写入持久队列，等待恢复后重放。"""
        if not self.message_queue:
            return
        try:
            msg = EnhancedAgentMessage.create_task_with_trace(
                sender="orchestrator",
                receiver=worker_name,
                task_type=intent,
                payload={"intent": intent, "slots": slots},
                trace_id=trace_id,
                ttl=600,
            )
            await self.message_queue.enqueue(worker_name, msg.to_json(), ttl=600)
            logger.info(f"Scheduler: task enqueued for offline worker '{worker_name}'")
        except Exception as e:
            logger.warning(f"Scheduler: failed to enqueue for '{worker_name}': {e}")

    async def _record_worker_metrics(self, worker_name: str, t_start: float,
                                      success: bool) -> None:
        """记录 Worker 调用耗时到 MetricsCollector。"""
        try:
            from common.monitor import metrics_collector
            duration_ms = int((time.time() - t_start) * 1000)
            await metrics_collector.record_worker_call(worker_name, duration_ms, success)
        except Exception:
            pass

    async def _do_request(self, worker_name: str, msg: AgentMessage) -> AgentMessage:
        """通过 MCP PubSub 发送请求并等待响应（含自动重试）。"""
        async def _send():
            return await self.pubsub.request_response(
                channel=f"agent:{worker_name}:inbox",
                message=msg,
                timeout=AGENT_BUS_CONFIG["default_task_timeout"],
            )

        max_retries = AGENT_BUS_CONFIG.get("max_retries", 2)
        return await ErrorHandler.with_retry(_send, max_retries=max_retries, backoff=1.0)
