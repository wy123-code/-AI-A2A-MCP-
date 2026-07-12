"""Agent 基类 —— BaseAgent（通用） + BaseWorkerAgent（领域子智能体）。

优化说明 (P0):
  - _dispatch 收到 task 后自动发送 ACK (status="received")，发送方短超时未收到则重试
  - request() 集成 ErrorHandler.with_retry，自动重试失败的请求
  - 错误响应自动携带 error_code 分类

优化说明 (P3):
  - BaseWorkerAgent 实现标准化五步闭环：validate → preprocess → execute → postprocess → output
  - _preprocess() / _postprocess() 提供 no-op 默认实现，子类按需覆盖
  - 集成 Redis 缓存层，execute() 调用前先查缓存
"""
import asyncio
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from loguru import logger

from config import AGENT_BUS_CONFIG
from agent_bus.message import AgentMessage, ERROR_CODE_WORKER_ERROR
from agent_bus.pubsub import MCPPubSub
from agent_bus.registry import AgentRegistry
from agent_bus.error_handler import ErrorHandler


class BaseAgent(ABC):
    """所有智能体的基类 —— 提供 MCP 通信、注册中心、心跳等通用能力。

    子类只需实现 handle_message() 即可通过 MCP 接收和响应消息。
    """

    def __init__(
        self,
        name: str,
        agent_type: str,
        pubsub: MCPPubSub,
        registry: AgentRegistry,
    ):
        self.name = name
        self.agent_type = agent_type
        self.pubsub = pubsub
        self.registry = registry
        self._running = False
        self._heartbeat_task: Optional[asyncio.Task] = None

    @abstractmethod
    async def handle_message(self, msg: AgentMessage) -> Optional[AgentMessage]:
        """处理收到的消息，可选择性地返回响应消息。

        Args:
            msg: 收到的 Agent 消息

        Returns:
            响应消息（如果该消息需要回复），或 None（fire-and-forget）
        """
        ...

    def inbox_channel(self) -> str:
        """返回 Agent 的 MCP 收件箱频道名，用于 Pub/Sub 通信。"""
        return f"agent:{self.name}:inbox"

    async def start(self) -> None:
        """启动 Agent：注册到注册中心、订阅收件箱、开始心跳。"""
        await self.registry.register(
            self.name, self.agent_type, intents=getattr(self, "supported_intents", [])
        )
        await self.pubsub.subscribe(self.inbox_channel(), self._dispatch)
        self._running = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info(f"[{self.name}] started, listening on '{self.inbox_channel()}'")

    async def stop(self) -> None:
        """停止 Agent：取消心跳、取消订阅、注销。"""
        self._running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        await self.pubsub.unsubscribe(self.inbox_channel())
        await self.registry.unregister(self.name)
        logger.info(f"[{self.name}] stopped")

    async def send_message(self, target: str, msg: AgentMessage) -> None:
        """向目标 Agent 发送单向消息（不等待响应）。"""
        await self.pubsub.publish(f"agent:{target}:inbox", msg)

    async def request(self, target: str, msg: AgentMessage, timeout: float = None) -> AgentMessage:
        """向目标 Agent 发送请求并等待响应（集成 ErrorHandler 重试）。

        Args:
            target: 目标 Agent 名称
            msg: 要发送的任务消息
            timeout: 超时时间（秒），默认使用配置值
        """
        timeout = timeout or AGENT_BUS_CONFIG["default_task_timeout"]

        async def _do_request():
            return await self.pubsub.request_response(
                channel=f"agent:{target}:inbox",
                message=msg,
                timeout=timeout,
            )

        max_retries = AGENT_BUS_CONFIG.get("max_retries", 2)
        return await ErrorHandler.with_retry(
            _do_request,
            max_retries=max_retries,
            backoff=1.0,
        )

    async def _dispatch(self, msg: AgentMessage) -> None:
        """内部回调：收到消息 → 发送 ACK → handle_message → 有响应则发送到响应频道。

        优化 (P0): 收到 task 后立即发送 ACK（status="received"），
        确保发送方在短超时内获得送达确认，避免不必要的重试。
        """
        try:
            # 立即发送 ACK 确认收到
            # ACK/response 频道必须以 inbox_channel 为前缀，与 pubsub.request_response 保持一致
            msg.mark_running()
            ack = AgentMessage.create_ack(msg)
            ack_channel = f"{self.inbox_channel()}:ack:{msg.correlation_id}"
            await self.pubsub.publish(ack_channel, ack)

            response = await self.handle_message(msg)
            if response:
                response_channel = f"{self.inbox_channel()}:response:{msg.correlation_id}"
                await self.pubsub.publish(response_channel, response)
        except Exception as e:
            logger.error(f"[{self.name}] handle_message failed: {e}", exc_info=True)
            error_response = AgentMessage.create_result(
                original_msg=msg,
                status="failed",
                payload={"success": False, "error": str(e), "data": []},
                error_code=ERROR_CODE_WORKER_ERROR,
            )
            try:
                response_channel = f"{self.inbox_channel()}:response:{msg.correlation_id}"
                await self.pubsub.publish(response_channel, error_response)
            except Exception:
                pass

    async def _heartbeat_loop(self) -> None:
        """后台心跳循环，定期更新注册中心心跳时间戳。"""
        interval = AGENT_BUS_CONFIG["agent_heartbeat_interval"]
        while self._running:
            try:
                await self.registry.heartbeat(self.name)
            except Exception as e:
                logger.warning(f"[{self.name}] heartbeat failed: {e}")
            await asyncio.sleep(interval)


class BaseWorkerAgent(BaseAgent):
    """领域业务工作 Agent 基类 —— 自动将 task 消息路由到 execute() 方法。

    实现标准化五步闭环：
      1. validate_intent()   → 意图校验
      2. _preprocess()       → 私有槽位补全（子类覆盖）
      3. execute()           → 领域工具调用（子类实现）
      4. _postprocess()      → 本地数据预处理（子类覆盖）
      5. WorkerResult        → 标准化结构化输出

    优化 (P3): 集成 Redis 缓存层 —— execute() 调用前先查缓存，命中直返。
    子类只需：
    1. 设置 supported_intents 列表
    2. 实现 execute(intent, slots, context) 方法（签名与现有工具函数兼容）
    3. 可选覆盖 _preprocess() / _postprocess() 添加领域定制逻辑
    """

    supported_intents: List[str] = []
    cache_ttl: int = 300  # 默认缓存 TTL（秒），子类可覆盖

    def __init__(self, name: str, pubsub: MCPPubSub, registry: AgentRegistry):
        super().__init__(name, "worker", pubsub, registry)
        self._cache: Any = None

    # ==================== 标准化闭环钩子 ====================

    async def _preprocess(self, intent: str, slots: Dict[str, Any],
                          context: Dict[str, Any] = None) -> Dict[str, Any]:
        """私有槽位补全 —— 子类覆盖以填入领域默认值。

        例如：天气 Worker 可在此处将缺失 date 补全为今天。
        默认实现：透传 slots 不做修改。
        """
        return slots

    async def _postprocess(self, result: Dict[str, Any], intent: str,
                           slots: Dict[str, Any]) -> Dict[str, Any]:
        """本地数据预处理 —— 子类覆盖以标准化/过滤/排序结果。

        默认实现：透传 result 不做修改。
        """
        return result

    # ==================== 缓存 ====================

    async def _ensure_cache(self) -> None:
        """懒加载 Redis 缓存连接。"""
        if self._cache is not None:
            return
        try:
            from cache.query_cache import QueryCache
            self._cache = QueryCache()
            await self._cache._ensure()
        except Exception:
            self._cache = None  # Redis 不可用时静默禁用缓存

    # ==================== 核心消息处理（闭环驱动） ====================

    async def handle_message(self, msg: AgentMessage) -> Optional[AgentMessage]:
        if msg.message_type != "task":
            return None

        intent = msg.task_type
        slots = msg.payload.get("slots", {})
        context = msg.payload.get("context", {})

        # --- 步骤 1: 意图校验 ---
        from .protocol import WorkerProtocol
        if not WorkerProtocol.validate_intent(intent, self.supported_intents):
            logger.warning(f"[{self.name}] intent not supported: {intent}")
            return AgentMessage.create_result(
                original_msg=msg,
                status="failed",
                payload={"success": False, "error": f"Intent not supported: {intent}", "data": []},
                error_code=ERROR_CODE_WORKER_ERROR,
            )

        # --- 步骤 2: 私有槽位补全 ---
        slots = await self._preprocess(intent, slots, context)

        # --- Worker 级缓存检查 ---
        await self._ensure_cache()
        if self._cache is not None:
            cache_params = {k: v for k, v in slots.items() if k != "_query"}
            cached = await self._cache.get(self.name, cache_params)
            if cached is not None:
                logger.info(f"[{self.name}] cache HIT for intent={intent}")
                cached["metadata"] = WorkerProtocol.build_metadata(
                    self.name, intent, result_count=len(cached.get("data", []) or []),
                    cache_hit=True,
                )
                return AgentMessage.create_result(
                    original_msg=msg,
                    status="success" if cached.get("success") else "failed",
                    payload=cached,
                )

        # --- 步骤 3: 执行领域业务 ---
        t0 = time.time()
        result = await self.execute(intent, slots, context)
        duration_ms = int((time.time() - t0) * 1000)

        # --- 步骤 4: 本地数据预处理 ---
        result = await self._postprocess(result, intent, slots)

        # --- 步骤 5: 标准化结构化输出 ---
        result_count = len(result.get("data", [])) if isinstance(result.get("data"), list) else 0
        result["metadata"] = WorkerProtocol.build_metadata(
            self.name, intent, duration_ms=duration_ms, result_count=result_count,
        )

        # --- 缓存写入 ---
        if self._cache is not None and result.get("success"):
            cache_params = {k: v for k, v in slots.items() if k != "_query"}
            await self._cache.set(self.name, cache_params, result, ttl=self.cache_ttl)

        return AgentMessage.create_result(
            original_msg=msg,
            status="success" if result.get("success") else "failed",
            payload=result,
        )

    @abstractmethod
    async def execute(
        self,
        intent: str,
        slots: Dict[str, Any],
        context: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """执行业务逻辑 —— 与现有 ITool 协议完全兼容。

        Args:
            intent: 意图标识
            slots: 槽位字典（已通过 _preprocess 补全）
            context: 额外上下文（如原始查询文本）

        Returns:
            {"success": bool, "error": str | None, "data": Any}
        """
        ...
